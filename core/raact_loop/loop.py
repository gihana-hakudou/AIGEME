"""RaAct 主循环 — raact_stream"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from core.engine.context import PromptAssembler
from core.engine.compressor import ContextCompressor
from core.engine.instructor_client import InstructorClient
from litellm.exceptions import ContextWindowExceededError
from core.engine.models import RaActResponse
from core.protocols.blocks import Block
from core.raact_loop.stream_router import route_response
from core.tools.registry import ToolRegistry
from core.tools.parallel import ParallelExecutor, ToolCallDef
from core.memory.memory_tracker import MemoryContextTracker

logger = logging.getLogger(__name__)

MAX_RAACT_ROUNDS = 8
MAX_MULTIMODAL_RETRIES = 1  # 多模态降级重试次数


def _strip_images_from_messages(messages: list[dict]) -> bool:
    """从 messages 中移除所有图片内容块。

    当 LLM API 不支持多模态时调用此函数降级。遍历每条消息，
    将其 content 中的 image_url 部分全部移除，保留纯文本。

    Returns:
        True 表示有图片被移除（messages 已被修改）
    """
    modified = False
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        # 过滤掉 image_url 类型的内容块
        text_parts = [p for p in content if p.get("type") != "image_url"]
        if len(text_parts) < len(content):
            modified = True
            if not text_parts:
                msg["content"] = "(图片已被移除，当前 API 不支持多模态)"
            elif len(text_parts) == 1 and text_parts[0].get("type") == "text":
                msg["content"] = text_parts[0].get("text", "")
            else:
                msg["content"] = text_parts
    return modified


def _is_multimodal_error(e: Exception) -> bool:
    """判断错误是否因 LLM API 不支持多模态（图片输入）导致"""
    err_str = str(e).lower()
    return any(kw in err_str for kw in [
        "image_url", "image input", "multimodal", "image is not",
        "unsupported image", "image content", "image type",
        "images are not supported", "does not support images",
        "does not support multimodal",
    ])


def _extract_tool_content(inner: Any, output_type: str = "json") -> str:
    """从工具返回值中提取有意义的文本内容给 LLM。

    Args:
        inner: 工具 execute() 返回的原始 dict（含 status/result 等字段）。
        output_type: 由 ToolRegistry 在返回包装中注明的输出类型，
                     取自 BaseTool.output_type。用于按工具类型精准解析。
                     可选值: "text" / "json" / "bash" / "file_read"
                            / "file_list" / "skill_search" / "skill_content"
                     默认 "json" 作安全兜底。

    Returns:
        非空字符串，保证 LLM 看到有意义的内容，不会因空消息无限循环。
    """
    # ── 0. 非 dict（None / str / 数值 / list）──────────
    if not isinstance(inner, dict):
        text = str(inner) if inner is not None else ""
        return text if text else "(empty output)"

    inner_status = inner.get("status", "")

    # ── 1. 工具内部非 ok 状态（通用，不受 output_type 影响）──
    if inner_status == "blocked":
        return f"命令被阻止: {inner.get('reason', '该操作不被允许')}"
    if inner_status == "deny":
        reason = inner.get("reason")
        return f"操作被拒绝: {reason}" if reason else "操作被拒绝"
    if inner_status == "needs_confirm":
        detail = inner.get("command") or inner.get("operation") or ""
        return f"操作需要用户确认: {detail}"
    if inner_status == "error":
        return f"错误: {inner.get('error', '未知错误')}"

    # ── 2. 工具成功 (status=ok) ──
    if inner_status == "ok":
        inner_result = inner.get("result")

        # 2a. result 为 None → 工具把数据放在顶层（如 servers/count），直接序列化
        if inner_result is None:
            # 去掉 status 和 output_type，序列化剩余内容
            content_dict = {k: v for k, v in inner.items() if k not in ("status", "output_type")}
            if content_dict:
                try:
                    return json.dumps(content_dict, ensure_ascii=False)
                except (TypeError, ValueError):
                    return str(content_dict)
            return "(操作成功，无返回内容)"

        # 2b. result 为字符串
        if isinstance(inner_result, str):
            # Bug 1 fix: 空字符串返回有意义提示
            return inner_result if inner_result else "(操作成功，无返回内容)"

        # 2c. result 为 dict → 按 output_type 精确解析
        if isinstance(inner_result, dict):
            # output_type = "bash" → 解析 stdout/stderr/returncode
            if output_type == "bash":
                stdout = inner_result.get("stdout", "")
                stderr = inner_result.get("stderr", "")
                rc = inner_result.get("returncode", 0)

                if stdout:
                    return stdout
                # Bug 2 fix: rc != 0 时拼接 stderr
                if rc != 0:
                    return f"(命令退出码: {rc}) {stderr}".strip()
                # Bug 2 fix: rc=0 但 stderr 有内容（警告等）
                if stderr:
                    return f"(命令成功，stderr: {stderr})"
                return "(命令执行成功，无输出)"

            # output_type = "file_read" → 展示文件摘要
            if output_type == "file_read":
                fpath = inner_result.get("file", "?")
                total = inner_result.get("total_lines", "?")
                start = inner_result.get("start_line", 1)
                end = inner_result.get("end_line", "?")
                returned = inner_result.get("returned_lines", "?")
                content = inner_result.get("content", "")
                truncated = inner_result.get("truncated", False)
                remaining = inner_result.get("remaining_lines", 0)
                lines = [
                    f"文件: {fpath}",
                    f"总行数: {total}",
                    f"读取范围: 第{start}-{end}行 ({returned}行)" + (f"，剩余{remaining}行" if truncated else ""),
                ]
                if content:
                    # 截断过长内容，LLM ToolMessage 有 500 字符上限
                    preview = content[:400]
                    lines.append(f"--- 内容预览 ---\n{preview}")
                return "\n".join(lines)

            # output_type = "file_list" → 目录列表
            if output_type == "file_list":
                dpath = inner_result.get("path", "?")
                files = inner_result.get("files", [])
                lines = [f"目录: {dpath}", f"共 {len(files)} 项"]
                for f in files[:30]:  # 最多列 30 项
                    icon = "📁" if f.get("type") == "dir" else "📄"
                    lines.append(f"  {icon} {f['name']}  ({f.get('size', 0)} B)")
                if len(files) > 30:
                    lines.append(f"  ... 还有 {len(files) - 30} 项")
                return "\n".join(lines)

            # output_type = "file_search" → 文件内文本搜索结果
            if output_type == "file_search":
                fpath = inner_result.get("file", "?")
                total = inner_result.get("total_lines", "?")
                query = inner_result.get("query", "")
                count = inner_result.get("match_count", 0)
                matches = inner_result.get("matches", [])
                ranges = inner_result.get("search_ranges", [])
                lines = [
                    f"文件: {fpath} (共{total}行)",
                    f"搜索: 「{query}」",
                    f"匹配: {count} 处",
                ]
                if ranges:
                    lines.append(f"分布范围: {'、'.join(ranges)}")
                if matches:
                    for m in matches[:20]:
                        content_preview = m.get("content", "")[:120]
                        lines.append(f"  第{m['line']}行: {content_preview}")
                    if inner_result.get("truncated_matches"):
                        lines.append(f"  ... 仅显示前 20 条，共 {count} 条匹配")
                return "\n".join(lines)

            # output_type = "skill_search" → 技能列表
            if output_type == "skill_search":
                results = inner_result.get("results", [])
                lines = [f"找到 {inner_result.get('count', len(results))} 个技能"]
                for r in results[:20]:
                    lines.append(f"  - {r.get('name', '?')}: {r.get('description', '')}")
                return "\n".join(lines)

            # output_type = "skill_content" → 技能详情
            if output_type == "skill_content":
                name = inner_result.get("name", "?")
                content = inner_result.get("content", "")
                return f"技能: {name}\n---\n{content}"

            # output_type = "image" → 图片读取结果（也支持浏览器截图的混合输出）
            if output_type == "image":
                fpath = inner_result.get("file", "?")
                width = inner_result.get("width", 0)
                height = inner_result.get("height", 0)
                size_kb = inner_result.get("size_kb", 0)
                lines = [
                    f"文件: {fpath}",
                    f"尺寸: {width}x{height}",
                    f"大小: {size_kb} KB",
                ]
                # 浏览器截图可能有 stdout/stderr
                stdout = inner_result.get("stdout", "")
                stderr = inner_result.get("stderr", "")
                extra = ""
                if stdout:
                    extra += f"\n--- stdout ---\n{stdout[:500]}"
                if stderr:
                    extra += f"\n--- stderr ---\n{stderr[:200]}"
                if extra:
                    lines.append(extra.rstrip())
                return "\n".join(lines)

            # 兜底：用字段启发式判断
            # 有 stdout 字段 → bash 风格（未声明 output_type 的旧工具）
            if "stdout" in inner_result:
                stdout = inner_result.get("stdout", "")
                stderr = inner_result.get("stderr", "")
                rc = inner_result.get("returncode", 0)
                if stdout:
                    return stdout
                if rc != 0:
                    return f"(命令退出码: {rc}) {stderr}".strip()
                if stderr:
                    return f"(命令成功，stderr: {stderr})"
                return "(命令执行成功，无输出)"

            # 兜底：有 message 字段 → 直接返回文本（避免 Qwen 等模型对空结果重复重试）
            if "message" in inner_result and "count" in inner_result and inner_result.get("count", 1) == 0:
                return inner_result["message"]

            # Bug 3 fix: json.dumps 加异常保护
            try:
                return json.dumps(inner_result, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(inner_result)

        # 2d. result 为其他可序列化类型（数值/list/bool）
        return str(inner_result)

    # ── 3. 无 status 字段或未知 status（兜底）────────
    try:
        return json.dumps(inner, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(inner)


class RaActLoop:
    """RaAct 推理-行动循环"""

    def __init__(
        self,
        instructor: InstructorClient,
        registry: ToolRegistry,
        prompt_assembler: PromptAssembler,
        memory_dir: Path | None = None,
        context_window: int = 128000,
        token_limit_ratio: float = 0.9,
        truncate_length: int = 500,
        keep_tool_turns: int = 5,
    ) -> None:
        self._instructor = instructor
        self._registry = registry
        self._prompt_assembler = prompt_assembler
        self._memory_dir = memory_dir          # MEMORY.md 所在目录
        self._compressor = ContextCompressor(
            context_window=context_window,
            token_limit_ratio=token_limit_ratio,
            instructor=instructor,
            truncate_length=truncate_length,
            keep_tool_turns=keep_tool_turns,
        )
        # 初始化 confirm/cancel 引用为 None，避免未调用 set_confirm_refs 时 AttributeError
        self._pending_confirm_ref = None
        self._confirm_result_ref = None
        self._reset_confirm_callback = None

        # 记忆去重追踪器（按需检索 + 去重注入）
        self._memory_tracker = MemoryContextTracker()

    def set_cancelled_ref(self, cancelled_ref) -> None:
        """设置取消状态引用（指向 session.cancelled）"""
        self._cancelled_ref = cancelled_ref

    def set_confirm_refs(self, pending_confirm_ref, confirm_result_ref, reset_confirm_callback=None) -> None:
        """设置确认对话框状态引用（指向 session.pending_confirm / session.confirm_result）

        reset_confirm_callback: 确认处理完成后重置 confirm_result 的回调
        """
        self._pending_confirm_ref = pending_confirm_ref
        self._confirm_result_ref = confirm_result_ref
        self._reset_confirm_callback = reset_confirm_callback

    @property
    def _cancelled(self) -> bool:
        """获取取消状态"""
        return getattr(self, '_cancelled_ref', lambda: False)()

    def _handle_cancelled_round(
        self,
        round_num: int,
        response: 'RaActResponse | None',
        messages: list,
    ) -> str:
        """处理用户强制取消：清理未执行工具、确保历史可持久化

        Returns:
            本轮 final_say
        """
        if response is None:
            return ""

        # 1. 从 messages 中移除本轮未执行的 tool_calls（最后一条 assistant 消息）
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant" and "tool_calls" in messages[i]:
                messages[i].pop("tool_calls", None)
                break

        # 2. 移除后续未完成的 tool_msg（assistant 之后的 tool 消息）
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant":
                j = i + 1
                while j < len(messages):
                    if messages[j].get("role") == "tool":
                        messages.pop(j)
                    else:
                        break
                break

        # 3. 返回取消提示作为 final_say
        hint = response.say or ""
        if hint:
            hint += "\n\n*用户强制取消了本轮输出*"
        else:
            hint = "*用户强制取消了本轮输出*"
        logger.info("RaAct 被用户取消（第%d轮），已清理未执行工具", round_num)
        return hint

    @property
    def _pending_confirm(self) -> asyncio.Event | None:
        """获取确认 Event"""
        ref = getattr(self, '_pending_confirm_ref', None)
        return ref() if ref else None

    @property
    def _confirm_result(self) -> str:
        """获取确认结果"""
        ref = getattr(self, '_confirm_result_ref', None)
        return ref() if ref else ""

    async def raact_stream(
        self,
        user_message: str,
        history: list[Any],
        send_block: Any,
        images: list[dict] | None = None,
    ) -> tuple[list[Any], str, str]:
        """
        RaAct 主循环。

        参数:
            user_message: 用户消息文本
            history: LangChain BaseMessage 列表
            send_block: 异步回调，用于推送 Block 到前端
            images: 图片列表，每项含 {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}

        返回:
            (updated_history, final_say)
        """
        # 构建 system prompt
        system_content = self._prompt_assembler.build_system_prompt()

        # 构建消息列表
        messages: list[dict] = [
            {"role": "system", "content": system_content},
        ]
        # 将 history 转换为 dict 格式
        for msg in history:
            d = self._msg_to_dict(msg)
            messages.append(d)

        # DIAG: 打印所有消息的角色和是否有 tool_calls
        for i, m in enumerate(messages):
            tc = "tool_calls" in m
            logger.info("[DIAG_MSG] [%d] role=%s tool_calls=%s content=%s",
                i, m.get("role"), tc, (m.get("content") or "")[:60])

        # ── 上下文压缩检查（Token 估算 + 结构性清理 + LLM 深度压缩） ──
        compressed_history, compression_prompt = await self._compressor.check_and_compress(history)
        if compressed_history is not history:
            # 用压缩后的 history 重建 messages（保留 system）
            messages = [messages[0]]  # 保留 system
            # 深度压缩时：先注入摘要，再追加保护轮次（旧历史已被摘要替代）
            if compression_prompt:
                messages.append({"role": "user", "content": compression_prompt})
                logger.info("已注入深度压缩摘要，等待 Agent 处理")
            for msg in compressed_history:
                messages.append(self._msg_to_dict(msg))
            logger.info("上下文压缩完成: 从 %d 条压缩至 %d 条", len(history), len(compressed_history))
            history[:] = compressed_history
        elif compression_prompt:
            # 仅注入摘要（未触发 trim_tools，但触发了深度压缩）
            messages.append({"role": "user", "content": compression_prompt})
            logger.info("已注入深度压缩摘要（仅摘要，history 未变化）")

        # 追加用户消息（支持多模态 content array）
        # 在用户消息前注入可变内容（时间等动态信息），不污染 system KV cache
        variable_content = self._prompt_assembler.build_variable_content()
        if variable_content:
            messages.append({"role": "user", "content": variable_content})

        # ── 记忆注入：每轮按需检索相关记忆 ──
        if (self._memory_dir and self._memory_dir.exists()
            and hasattr(self, '_memory_tracker')):
            try:
                memory_tool = self._registry.get("memory")
                memory_context = await self._memory_tracker.search_new_memory(
                    user_message=user_message,
                    round_num=1,
                    memory_dir=self._memory_dir,
                    memory_tool=memory_tool,
                )
                if memory_context:
                    context_text = MemoryContextTracker.get_context_text(memory_context)
                    if context_text:
                        messages.append({"role": "user", "content": context_text})
            except Exception as e:
                logger.warning("[MEMORY_TRACKER] 记忆注入异常: %s", e)

        if images:
            content_parts: list[dict] = [{"type": "text", "text": user_message}]
            content_parts.extend(images)
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_message})

        # 记录当前轮次 in-loop 消息起始位置（排除 system + history + user）
        current_round_start = len(messages)

        # 当前轮次的对话拼接结果
        round_messages: list[Any] = []
        final_say: str | None = None
        last_response: RaActResponse | None = None

        # 跨轮累积的 reasoning 和 say（持久化时用完整内容）
        accumulated_reasoning: list[str] = []
        accumulated_say: list[str] = []

        # 记录本轮所有工具交互（用于历史）
        tool_interactions: list[dict] = []

        # 多模态降级重试计数器（跨 round 共享）
        _multimodal_retries = 0

        for round_num in range(1, MAX_RAACT_ROUNDS + 1):
            # [检查点1] round 循环开始前
            if self._cancelled:
                logger.debug("RaAct 被取消（检查点1）")
                final_say = self._handle_cancelled_round(round_num, last_response, messages)
                break

            logger.debug("RaAct round %d/%d", round_num, MAX_RAACT_ROUNDS)

            # 最后一轮：注入结束提示 + tool_choice=none，让 LLM 自然总结退出
            _last_round = round_num == MAX_RAACT_ROUNDS
            if _last_round:
                _summary_msg = {
                    "role": "user",
                    "content": "这是最后一轮，请总结当前进度和结果，不需要再调用工具。",
                }
                messages.append(_summary_msg)
                logger.info("RaAct 最后一轮，注入总结提示词 + tool_choice=none")

            # 计算本轮 tool_choice
            _tc = None
            if _last_round:
                _tc = "none"
            elif getattr(self._prompt_assembler, '_force_memory_tool', False):
                # 触发记忆整理提醒 → 强制调 memory 工具，让 agent 实际执行整理
                _tc = {"type": "function", "function": {"name": "memory"}}
                self._prompt_assembler._force_memory_tool = False
                logger.info("强制 tool_choice=memory（记忆整理周期触发）")

            # 调用 Instructor（流式推送 thinking/speech 到前端）
            response: RaActResponse | None = None
            try:
                response = await self._instructor.create_completion_stream(
                    messages=[self._dict_to_message(m) for m in messages],
                    send_block=send_block,
                    tools=self._registry.schemas,
                    cancelled_check=lambda: self._cancelled,
                    tool_choice=_tc,
                )
            except ContextWindowExceededError as e:
                logger.warning("上下文窗口超限: %s，强制丢弃旧消息后重试", e)
                # 用 got_error=True 强制丢弃最旧的消息直到 Token 安全（不触发 Agent 深度压缩，
                # 因为压缩指令本身也占 Token，超限时注入会让情况更糟）
                compressed, _ = await self._compressor.check_and_compress(
                    history, got_error=True,
                )
                if compressed is not history:
                    # 重建 messages：保留 system，追加压缩后的 history
                    messages = [messages[0]]
                    for msg in compressed:
                        messages.append(self._msg_to_dict(msg))
                    history[:] = compressed
                    # 重新追加用户消息
                    if images:
                        cp = [{"type": "text", "text": user_message}]
                        cp.extend(images)
                        messages.append({"role": "user", "content": cp})
                    else:
                        messages.append({"role": "user", "content": user_message})
                    logger.info("强制丢弃旧消息后重试 LLM 调用（保留 %d 条）", len(compressed))
                    continue  # 重试当前轮
                # 压缩后仍未解决 → 发错误 block 给前端并结束
                await send_block(Block(
                    block_type="error",
                    delta=f"[上下文超限] 对话历史过长，已尝试压缩但仍超出模型上下文窗口。请开启新对话。",
                    is_final=True,
                ))
                if last_response:
                    final_say = last_response.say or ""
                break
            except Exception as e:
                import traceback as _tb
                err_type = type(e).__name__
                err_str = str(e)
                err_lower = err_str.lower()
                # 尝试获取实际连接的 api_base（方便诊断）
                api_base_hint = ""
                try:
                    api_base_hint = f" (地址: {self._instructor._api_base})"
                except Exception:
                    pass
                logger.error("Instructor 调用失败 [%s]: %s\n%s", err_type, err_str, _tb.format_exc())

                # ── 多模态降级检测：如果 API 不支持图片，移除图片后重试 ──
                if _multimodal_retries < MAX_MULTIMODAL_RETRIES and _is_multimodal_error(e):
                    if _strip_images_from_messages(messages):
                        _multimodal_retries += 1
                        logger.info(
                            "检测到 API 不支持多模态，已移除图片消息，第 %d/%d 次重试",
                            _multimodal_retries, MAX_MULTIMODAL_RETRIES,
                        )
                        continue  # 重试当前 round
                    logger.info("多模态错误但未找到图片消息，不重试")

                # 生成用户可读的错误描述
                # 常见错误类型映射
                if "AuthenticationError" in err_type or "401" in err_str or "invalid_api_key" in err_lower:
                    user_msg = f"[API 认证失败] API Key 无效或未设置，请在设置页检查 API Key。"
                elif "RateLimitError" in err_type or "429" in err_str or "rate_limit" in err_lower:
                    user_msg = f"[速率限制] 请求过于频繁，请稍候再试。"
                elif ("ConnectionError" in err_type or "ConnectError" in err_type
                      or "connect" in err_lower or "InternalServerError" in err_type and "connect" in err_lower):
                    user_msg = f"[连接失败] 无法连接到 LLM 服务{api_base_hint}，请检查 API Base URL 或网络。"
                elif "Timeout" in err_type or "timeout" in err_lower:
                    user_msg = f"[请求超时] LLM 服务响应超时{api_base_hint}，请稍候重试。"
                elif "NotFoundError" in err_type or "404" in err_str or "model_not_found" in err_lower:
                    user_msg = f"[模型不存在] 找不到指定模型{api_base_hint}，请在设置页检查模型名称。"
                elif "BadRequestError" in err_type or "400" in err_str:
                    user_msg = f"[请求错误] LLM 服务拒绝了请求{api_base_hint}。错误: {err_str[:150]}"
                elif "InternalServerError" in err_type or "500" in err_str:
                    user_msg = f"[服务端错误] LLM 服务内部错误{api_base_hint}，请稍候重试。"
                else:
                    user_msg = f"[LLM 请求失败] {err_type}: {err_str[:200]}"

                await send_block(Block(
                    block_type="error",
                    delta=user_msg,
                    is_final=True,
                ))

                if last_response:
                    final_say = last_response.say or ""
                break

            if response is None:
                break

            logger.info(
                "RaAct response: reasoning=%s, say=%s, tool_calls=%s",
                response.reasoning[:50] if response.reasoning else "None",
                response.say[:80] if response.say else "None",
                len(response.tool_calls) if response.tool_calls else 0,
            )

            # [检查点2] LLM 调用后，跳过 tool 循环
            if self._cancelled:
                logger.debug("RaAct 被取消（检查点2）")
                final_say = self._handle_cancelled_round(round_num, response, messages)
                break

            last_response = response

            # 检查是否有工具调用
            has_tool_calls = bool(response.tool_calls)

            # 跨轮累积 reasoning 和 say
            # 有 tool_calls 的轮次不累积 say（中间轮只输出 reasoning + tool_calls）
            if response.reasoning:
                accumulated_reasoning.append(response.reasoning)
            if not has_tool_calls and response.say:
                accumulated_say.append(response.say)

            if has_tool_calls and response.tool_calls:
                # 先追加 assistant 消息（含 tool_calls），再追加 tool result
                # 这是 OpenAI/DeepSeek API 要求的顺序
                assistant_msg = self._build_assistant_message(response, has_tool_calls=True, round_num=round_num)
                messages.append(assistant_msg)

                # ════════════════════════════════════════════════════════════
                # Step A: 推送所有 tool_call blocks（保持现有格式）
                # ════════════════════════════════════════════════════════════
                tool_call_ids = []
                for i, tc in enumerate(response.tool_calls):
                    tool_call_id = tc.id if tc.id else f"call_{round_num}_{i}"
                    tool_call_ids.append(tool_call_id)

                    # 推送 tool_call block（含参数摘要以便用户了解工具意图）
                    args_preview = ""
                    if tc.arguments:
                        skip_keys = {"_confirmed"}
                        preview_parts = []
                        for k, v in tc.arguments.items():
                            if k in skip_keys:
                                continue
                            if isinstance(v, str) and len(v) > 40:
                                v = v[:37] + "..."
                            preview_parts.append(f"{k}={v}")
                        args_preview = "(" + ", ".join(preview_parts) + ")"
                    await send_block(
                        Block(
                            block_type="tool_call",
                            delta=f"{tc.name}{args_preview}",
                            metadata={"args": tc.arguments},
                        )
                    )

                # ════════════════════════════════════════════════════════════
                # Step B: ParallelExecutor 并行执行所有工具
                # ════════════════════════════════════════════════════════════
                tc_defs = [
                    ToolCallDef(name=tc.name, arguments=tc.arguments, id=tc.id)
                    for tc in response.tool_calls
                ]
                parallel_exec = ParallelExecutor(self._registry)
                parallel_results = await parallel_exec.execute(tc_defs)

                logger.info("[TOOL_DEBUG] 并行执行完成: %d 个工具, statuses=%s",
                    len(parallel_results),
                    json.dumps([r.get("status") for r in parallel_results], ensure_ascii=False))

                # ════════════════════════════════════════════════════════════
                # Step C: 处理 needs_confirm — 逐个处理确认请求
                # ════════════════════════════════════════════════════════════
                for i, (tc, result) in enumerate(zip(response.tool_calls, parallel_results)):
                    if result.get("status") != "needs_confirm":
                        # 不需要确认的工具 — 重置 confirm_result 防污染后续
                        if self._reset_confirm_callback:
                            self._reset_confirm_callback()
                        continue

                    # 构建清晰的确认信息，说明 agent 想要做什么
                    tool_name = tc.name
                    operation = result.get("command") or result.get("operation", "")
                    args_preview = ""
                    if tc.arguments:
                        # 通用化参数预览：显示所有非敏感参数
                        skip_keys = {"_confirmed"}
                        preview_parts = []
                        for k, v in tc.arguments.items():
                            if k in skip_keys:
                                continue
                            if isinstance(v, str) and len(v) > 60:
                                v = v[:57] + "..."
                            preview_parts.append(f"{k}={v}")
                        args_preview = ", ".join(preview_parts)
                    confirm_msg = (
                        f"Agent **{tool_name}** 想要执行 `{operation}` 操作"
                        + (f"\n参数: `{args_preview}`" if args_preview else "")
                    )
                    # 修复竞态：先清除 Event，再推送 confirm block
                    # 避免 confirm_response 在 send_block 和 clear() 之间到达导致死锁
                    pc = self._pending_confirm
                    if pc:
                        pc.clear()
                    await send_block(Block(
                        block_type="confirm",
                        delta=confirm_msg,
                        metadata={
                            "full_result": result,
                            "tool_name": tc.name,
                            "session_id": self._registry.session_id,
                        },
                    ))
                    # 等待前端确认：使用 asyncio.Event（由 ws_server 的 confirm_response 设置）
                    if pc:
                        confirm_received = False
                        try:
                            await asyncio.wait_for(pc.wait(), timeout=60.0)
                            confirm_received = True
                        except asyncio.TimeoutError:
                            confirm_received = False
                            logger.warning("确认操作超时（60秒），自动取消")
                        if not confirm_received:
                            parallel_results[i] = {"status": "cancelled", "error": "确认超时"}
                        else:
                            if self._confirm_result == "confirm":
                                logger.info("[TOOL_DEBUG] 用户确认操作: %s, 开始重新执行工具", tc.name)
                                new_result = await self._registry.execute(
                                    tc.name, tc.arguments, _confirmed=True
                                )
                                parallel_results[i] = new_result
                                logger.info("[TOOL_DEBUG] 工具重新执行完成: status=%s, result_type=%s",
                                    new_result.get("status"), type(new_result.get("result")).__name__)
                            else:
                                logger.info("[TOOL_DEBUG] 用户取消操作: %s", tc.name)
                                parallel_results[i] = {
                                    "status": "cancelled",
                                    "error": f"用户取消了 {tool_name} 的 {operation} 操作",
                                }

                    # 重置 confirm_result 以防交叉污染后续工具确认
                    if self._reset_confirm_callback:
                        self._reset_confirm_callback()

                # [检查点3] 工具执行 + 确认处理完成后
                if self._cancelled:
                    logger.debug("RaAct 被取消（检查点3）")
                    final_say = self._handle_cancelled_round(round_num, response, messages)
                    break

                # ════════════════════════════════════════════════════════════
                # Step D: 推送 tool_result + 构造 tool_msg（保持原始顺序）
                # ════════════════════════════════════════════════════════════
                for i, (tc, result) in enumerate(zip(response.tool_calls, parallel_results)):
                    tool_call_id = tool_call_ids[i]

                    # 提取 tool_content（统一处理 ok / error / cancelled 等状态）
                    inner = result.get("result", {})
                    output_type = result.get("output_type", "json")
                    if result.get("status") == "ok":
                        tool_content = _extract_tool_content(inner, output_type=output_type)
                        logger.info("[TOOL_DEBUG] 提取内容: inner=%s, output_type=%s, tool_content=%s",
                            type(inner).__name__, output_type, tool_content[:80])
                    else:
                        err_msg = result.get("error") or result.get("reason") or "未知错误"
                        tool_content = f"错误: {err_msg}"
                        logger.info("[TOOL_DEBUG] 工具错误: %s", err_msg)

                    # 推送 tool_result block（复用 tool_content 保持一致）
                    result_summary = tool_content[:200]
                    await send_block(
                        Block(
                            block_type="tool_result",
                            delta=result_summary,
                            metadata={"full_result": result},
                        )
                    )

                    # 构造 tool_msg 加入 messages
                    tool_msg = {
                        "role": "tool",
                        "content": tool_content,
                        "tool_call_id": tool_call_id,
                    }
                    logger.info("[TOOL_DEBUG] 构造 tool_msg: role=tool, content=%s, tool_call_id=%s",
                        tool_msg["content"][:80], tool_call_id)
                    messages.append(tool_msg)

                    # ── 图片读取结果：注入多模态 user 消息让 LLM 能"看到"图片 ──
                    if (result.get("status") == "ok"
                        and output_type == "image"
                        and isinstance(inner, dict)
                        and result.get("_ss_data_url")):
                        data_url = result["_ss_data_url"]
                        file_path = inner.get("file", "")
                        img_user_msg = {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": f"这是我刚才读取的图片文件：{file_path}，请分析它的内容。"},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                        messages.append(img_user_msg)
                        logger.info("[TOOL_DEBUG] 图片已注入多模态消息: %s (%dx%d)",
                            file_path, inner.get("width", 0), inner.get("height", 0))

                    # 保留 tool_name 供 persistence/PAE 检测用
                    tool_msg_with_name = dict(tool_msg, tool_name=tc.name)
                    tool_interactions.append(tool_msg_with_name)

                # 本轮调用了记忆工具 → 重置整理提醒计数器
                # （agent 既然主动写了记忆，就不需要再提醒了）
                if any(tc.name == "memory" for tc, _ in zip(response.tool_calls, parallel_results)):
                    self._prompt_assembler.reset_organize_counter()

            else:
                # 无工具调用 → 结束循环
                final_say = "\n".join(accumulated_say) if accumulated_say else (response.say or "")
                logger.info("[TOOL_DEBUG] 模型无工具调用，结束循环。final_say=%s", final_say[:80] if final_say else "None")
                # 将最终回复加入 messages，确保被 round_messages 捕获和持久化
                assistant_msg = self._build_assistant_message(response, has_tool_calls=False, round_num=round_num)
                messages.append(assistant_msg)
                break

            # 第二轮调用前打印消息列表（看 LLM 到底收到了什么）
            logger.info("[TOOL_DEBUG] === 第%d轮消息列表 ===", round_num + 1)
            for mi, m in enumerate(messages):
                role = m.get("role", "?")
                content = m.get("content", "")
                if role == "tool":
                    logger.info("[TOOL_DEBUG]   [%d] %s: content=%s, tool_call_id=%s",
                        mi, role, content[:80], m.get("tool_call_id", ""))
                elif role == "assistant" and "tool_calls" in m:
                    logger.info("[TOOL_DEBUG]   [%d] %s: content=%s, tool_calls=%s",
                        mi, role, content[:50] if content else "(empty)",
                        json.dumps(m["tool_calls"], ensure_ascii=False)[:200])
                else:
                    logger.info("[TOOL_DEBUG]   [%d] %s: content=%s", mi, role, content[:60] if content else "(empty)")

            if round_num == MAX_RAACT_ROUNDS:
                # 最后一条消息已经是总结提示，tool_choice=none 保证不会再有工具调用
                # 循环会自然结束（无 tool_calls → break）
                logger.info("RaAct 达到最大轮数 %d，等待 LLM 输出总结", MAX_RAACT_ROUNDS)
                break

        # 拼接本轮结果到 history（仅当前轮次的 in-loop 消息，不包含历史）
        # 从 current_round_start 位置开始提取，排除 system + history + user
        in_loop_started = False
        round_messages = []
        for m in messages[current_round_start:]:
            role = m.get("role", "")
            if not in_loop_started:
                if role == "assistant":
                    in_loop_started = True
                else:
                    continue
            converted = self._dict_to_message(m)
            round_messages.append(converted)

        # 发送 turn_end 信号（携带 cancelled 标记）
        cancelled_flag = self._cancelled
        await send_block(
            Block(block_type="turn_end", delta="", is_final=True, metadata={"cancelled": cancelled_flag})
        )

        return round_messages, final_say or "", accumulated_reasoning

    def _build_assistant_message(
        self, response: RaActResponse, has_tool_calls: bool, round_num: int = 1
    ) -> dict:
        """构建 assistant 消息字典（包含 tool_calls，ID 与 tool_msg 匹配）"""
        msg: dict = {
            "role": "assistant",
            "content": "" if has_tool_calls else (response.say or ""),
        }
        # preserve_thinking: 将 reasoning 作为 reasoning_content 传入，供下一轮 LLM 使用
        # DeepSeek V4 规范: 有 tool_calls 的轮次必须回传 reasoning_content，否则 API 返回 400；
        # 无 tool_calls 的轮次传入会被忽略（回传了也不报错），为简化逻辑直接全量回传
        preserve = getattr(self._instructor, 'preserve_thinking', False)
        if preserve and response.reasoning:
            msg["reasoning_content"] = response.reasoning
        if has_tool_calls and response.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id if tc.id else f"call_{round_num}_{i}", "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for i, tc in enumerate(response.tool_calls)
            ]
        return msg

    def _msg_to_dict(self, msg: Any) -> dict:
        """将 BaseMessage 转换为字典（适配 OpenAI/DeepSeek 格式）"""
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        role_map = {
            HumanMessage: "user",
            AIMessage: "assistant",
            SystemMessage: "system",
            ToolMessage: "tool",
        }
        role = role_map.get(type(msg))
        if role is None:
            logger.warning("_msg_to_dict: unknown message type %s", type(msg).__name__)
            content = getattr(msg, "content", str(msg))
            role = "user"
        d: dict = {"role": role, "content": getattr(msg, "content", "") or ""}

        # preserve_thinking: 从 additional_kwargs 提取 reasoning_content
        preserve = getattr(self._instructor, 'preserve_thinking', False) if hasattr(self, '_instructor') else False
        if isinstance(msg, AIMessage) and msg.additional_kwargs:
            if msg.additional_kwargs.get("tool_calls"):
                d["tool_calls"] = msg.additional_kwargs["tool_calls"]
            if preserve and msg.additional_kwargs.get("reasoning_content"):
                d["reasoning_content"] = msg.additional_kwargs["reasoning_content"]

        # tool 消息：保留 tool_call_id
        if isinstance(msg, ToolMessage) and hasattr(msg, "tool_call_id"):
            d["tool_call_id"] = msg.tool_call_id

        # 历史消息包装为 content array + cache_control（仅 Anthropic，其他 provider 用字符串格式）
        raw = d["content"]
        if isinstance(raw, str) and raw:
            provider = self._instructor._get_provider() if hasattr(self, '_instructor') else None
            if provider == "anthropic":
                d["content"] = [
                    {
                        "type": "text",
                        "text": raw,
                        "cache_control": {"type": "ephemeral"},
                    },
                ]

        return d

    def _dict_to_message(self, d: dict) -> Any:
        """将字典转换为 LangChain BaseMessage"""
        role = d.get("role", "user")
        content = d.get("content", "")
        if role == "user":
            return HumanMessage(content=content)
        if role == "assistant":
            msg = AIMessage(content=content)
            if "tool_calls" in d:
                msg.additional_kwargs["tool_calls"] = d["tool_calls"]
            # preserve_thinking: 将 reasoning_content 存入 additional_kwargs 供下一轮 LLM
            preserve = getattr(self._instructor, 'preserve_thinking', False) if hasattr(self, '_instructor') else False
            if preserve and "reasoning_content" in d:
                msg.additional_kwargs["reasoning_content"] = d["reasoning_content"]
            return msg
        if role == "system":
            from langchain_core.messages import SystemMessage
            return SystemMessage(content=content)
        if role == "tool":
            from langchain_core.messages import ToolMessage
            return ToolMessage(
                content=content,
                tool_call_id=d.get("tool_call_id", ""),
            )
        return HumanMessage(content=content)
