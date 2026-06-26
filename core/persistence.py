"""对话持久化 — 两层存储（data + meta），turn_end 追加写入"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from core.engine.compressor import trim_tools
from core.tools.file_lock import acquire_file_lock

logger = logging.getLogger(__name__)


class Persistence:
    """对话持久化管理器"""

    def __init__(
        self,
        data_dir: Path,
        user_id: str = "local",
        char_id: str = "ario",
        max_turns: int = 50,
        max_file_records: int = 1000,
        keep_tool_turns: int = 10,
        truncate_tool_content_length: int = 500,
    ) -> None:
        self._conv_dir = data_dir / user_id / char_id / "conversations"
        self._conv_dir.mkdir(parents=True, exist_ok=True)
        self._max_turns = max_turns
        self._max_file_records = max_file_records
        self._keep_tool_turns = keep_tool_turns
        self._truncate_tool_content_length = truncate_tool_content_length

    @staticmethod
    def _extract_last_turns(records: list[dict], max_turns: int) -> list[dict]:
        """从记录列表中提取最近 N 个完整轮次（按 user 消息分隔）"""
        if not records:
            return []

        # 从后往前找 user 消息的位置
        user_positions = []
        for i in range(len(records) - 1, -1, -1):
            if records[i].get("data", {}).get("role") == "user":
                user_positions.append(i)
                if len(user_positions) >= max_turns:
                    break

        if not user_positions:
            return records

        start = user_positions[-1]
        return records[start:]

    async def load_recent_history(self) -> list[BaseMessage]:
        """加载最近 max_turns 轮对话，然后清理旧轮次 tools"""
        files = sorted(self._conv_dir.glob("*.json"))
        if not files:
            return []

        records: list[dict] = []
        # 分卷文件（带数字）按文件名升序 → 主文件（不带数字）最后
        # conversations_001.json(最旧) → _002.json → conversations.json(最新)
        split_files = sorted(f for f in files if "_" in f.stem)
        main_files = [f for f in files if "_" not in f.stem]
        for f in split_files + main_files:
            try:
                data = json.loads(f.read_text("utf-8"))
                if isinstance(data, list):
                    records.extend(data)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("读取对话文件失败 %s: %s", f, e)

        # 按轮次提取 max_turns 轮
        recent = self._extract_last_turns(records, self._max_turns)
        logger.info(
            "加载对话历史: %d records → %d records (%d turns)",
            len(records), len(recent), self._max_turns,
        )

        # 转换为 LLM 消息
        messages = self._load_llm_messages(recent)

        # 清理旧轮次 tools（保持最近 keep_tool_turns 轮完整）
        return trim_tools(messages, self._keep_tool_turns, self._truncate_tool_content_length)

    async def save_turn(
        self,
        role: str,
        content: str,
        meta: dict | None = None,
        **kwargs: Any,
    ) -> dict:
        """保存一轮对话记录（加文件锁防止并发写冲突）"""
        now = datetime.now()
        record = {
            "turn_id": f"turn_{now:%Y%m%d_%H%M%S%f}",
            "timestamp": now.isoformat(),
            "data": {
                "role": role,
                "content": content,
                **kwargs,
            },
            "meta": {
                "system_time": now.strftime("%H:%M:%S"),
                **(meta or {}),
            },
        }

        date_str = now.strftime("%Y-%m-%d")
        file_path = self._conv_dir / "conversations.json"

        lock = await acquire_file_lock(file_path)
        async with lock:
            # 读取现有记录
            records: list[dict] = []
            if file_path.exists():
                try:
                    records = json.loads(file_path.read_text("utf-8"))
                except (json.JSONDecodeError, OSError):
                    records = []

            records.append(record)

            # 文件过长时分割
            if len(records) > self._max_file_records:
                await self._split_file("conversations", records, file_path)
            else:
                file_path.write_text(
                    json.dumps(records, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    async def _split_file(self, base_name: str, records: list[dict], original_path: Path) -> None:
        """文件超过上限时分割（加文件锁）

        将全量记录写入新的分卷文件，然后清空主文件避免后续每次写入都触发分割。
        """
        # 查找已有分卷编号
        existing = list(self._conv_dir.glob(f"{base_name}_*.json"))
        next_num = max((int(f.stem.split("_")[-1]) for f in existing), default=0) + 1

        # 将当前记录写入新分卷
        new_path = self._conv_dir / f"{base_name}_{next_num:03d}.json"
        lock = await acquire_file_lock(new_path)
        async with lock:
            new_path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # 清空主文件，避免后续写入重复触发分割
        original_path.write_text("[]", encoding="utf-8")

    @staticmethod
    def _load_llm_messages(records: list[dict]) -> list[BaseMessage]:
        """加载时只取 data 层，去掉 meta 层"""
        messages: list[BaseMessage] = []
        for r in records:
            data = r.get("data", {})
            role = data.get("role", "user")
            content = data.get("content", "")

            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                msg = AIMessage(content=content)
                if data.get("reasoning"):
                    msg.additional_kwargs["reasoning"] = data["reasoning"]
                # 恢复 tool_calls（确保 assistant→tool 顺序正确）
                if data.get("tool_calls"):
                    msg.additional_kwargs["tool_calls"] = data["tool_calls"]
                messages.append(msg)
            elif role == "tool":
                msg = ToolMessage(
                    content=data.get("content", ""),
                    tool_call_id=data.get("tool_call_id", "unknown"),
                )
                tool_name = data.get("tool_name", "")
                if tool_name:
                    msg.additional_kwargs["tool_name"] = tool_name
                messages.append(msg)

        # 清理末尾孤立的 tool_calls（没有对应 ToolMessage 的 AI 消息）
        # 避免 API（DeepSeek/OpenAI）报 "must be followed by tool messages"
        messages = Persistence._clean_orphan_tool_calls(messages)
        return messages

    @staticmethod
    def _clean_orphan_tool_calls(
        messages: list[BaseMessage],
    ) -> list[BaseMessage]:
        """清理截断导致末尾孤立的 tool_calls

        OpenAI/DeepSeek API 要求：assistant 消息包含 tool_calls 时，
        下一条必须是 tool 消息。如果加载的历史在被截断后末尾只剩下
        一个带 tool_calls 的 assistant 消息（无对应的 tool 响应），
        会导致 API 拒绝请求。
        """
        if not messages:
            return messages

        # 从后往前扫描，找到最后一个 assistant(tool_calls) 和 tool 的配对情况
        # 如果末尾遗留了 tool_calls 但无对应 tool，清理之
        found_tool_calls: set[str] = set()
        found_tool_ids: set[str] = set()
        orphan_indices: list[int] = []

        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if isinstance(msg, ToolMessage) and hasattr(msg, "tool_call_id"):
                found_tool_ids.add(msg.tool_call_id)
            elif isinstance(msg, AIMessage):
                tcs = msg.additional_kwargs.get("tool_calls", [])
                for tc in tcs:
                    tc_id = ""
                    if isinstance(tc, dict):
                        tc_id = tc.get("id", "")
                    elif hasattr(tc, "id"):
                        tc_id = tc.id  # type: ignore[union-attr]
                    if tc_id:
                        if tc_id not in found_tool_ids:
                            orphan_indices.append(i)
                            break
                        found_tool_calls.add(tc_id)

        if not orphan_indices:
            return messages

        # 从后往前清理孤儿 assistant（不影响索引）
        result = list(messages)
        for idx in orphan_indices:
            msg = result[idx]
            if isinstance(msg, AIMessage):
                # 清理 tool_calls，保留 content（如果有的话）
                msg.additional_kwargs.pop("tool_calls", None)
                logger.info(
                    "清理孤立的 tool_calls: [%d] content=%s",
                    idx, (msg.content or "")[:80],
                )

        return result
