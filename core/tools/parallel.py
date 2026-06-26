"""ParallelExecutor — 并行工具调度器

将 LLM 返回的多个并行 tool_calls 按读/写/复合分组后调度执行。
取代 loop.py 中原有的串行 `for tc in response.tool_calls:` 模式。

设计要点：
  - READ 组：asyncio.gather 全并行
  - WRITE 组：文件级锁调度，同文件路径串行，不同文件路径并行
  - COMPOUND 组：顺序串行执行
  - 返回顺序与输入 tool_calls 严格一致（LLM API 协议要求）
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.tools.registry import ToolRegistry
from core.protocols.blocks import Block

logger = logging.getLogger(__name__)


# ── 数据模型 ──────────────────────────────────────────────────────────────


@dataclass
class ToolCallDef:
    """工具调用定义（由 LLM 返回）"""
    name: str          # 工具名 (如 "memory", "document")
    arguments: dict    # 参数字典
    id: str | None = None  # tool_call_id


@dataclass
class ParallelResult:
    """并行执行结果"""
    index: int         # 原始 tool_calls 列表中的索引
    result: dict       # execute 返回结果
    error: str | None = None  # 错误信息


# ── 工具分类常量 ───────────────────────────────────────────────────────────

# memory 按 operation 分类
_MEMORY_READ_OPS: frozenset[str] = frozenset({
    "read", "search", "list", "check_similar", "graph_search",
})
_MEMORY_WRITE_OPS: frozenset[str] = frozenset({
    "add", "edit", "del", "link",
})
_MEMORY_COMPOUND_OPS: frozenset[str] = frozenset({
    "audit", "merge", "refactor", "prune",
})

# document 按 operation 分类（read/search 是读，其余是写）
_DOCUMENT_READ_OPS: frozenset[str] = frozenset({"read", "search"})


# ── 工具分类辅助 ───────────────────────────────────────────────────────────


def _classify_tool(name: str, arguments: dict) -> str:
    """判定单个工具所属类别: "read" | "write" | "compound" """
    if name == "memory":
        op = arguments.get("operation", "")
        if op in _MEMORY_READ_OPS:
            return "read"
        if op in _MEMORY_WRITE_OPS:
            return "write"
        if op in _MEMORY_COMPOUND_OPS:
            return "compound"
        # 未知 operation 保守走 compound
        logger.warning("[PARALLEL] memory 未知 operation=%s, 按 compound 处理", op)
        return "compound"

    if name == "document":
        op = arguments.get("operation", "")
        if op in _DOCUMENT_READ_OPS:
            return "read"
        # read_image 在语义上是读取行为，也归为 read
        if op == "read_image":
            return "read"
        # 其余 document 操作（write/append/edit/delete/list）归为 write
        return "write"

    # shell 类工具始终串行
    if name in ("bash", "python", "powershell", "cmd", "sh"):
        return "compound"

    # 未知工具 — 默认走 compound（安全保守）
    logger.warning("[PARALLEL] 未知工具 name=%s, 按 compound 处理", name)
    return "compound"


def _resolve_resource_path(name: str, arguments: dict) -> str | None:
    """解析工具操作的资源路径，用于写操作的分组调度。

    返回:
        str  : 可排序/分组的资源标识符
        None : 无法确定资源路径（该工具单独串行）
    """
    if name == "document":
        path = arguments.get("path")
        if path:
            return str(Path(path).resolve())
        # write 无 path 时自动生成，路径不确定，视为独立资源
        return None

    if name == "memory":
        title = arguments.get("title")
        if title:
            # 不同角色的 memory 目录不同，但此处不感知角色 ID
            # 使用虚拟路径 memory:{title} 作为分组键
            return f"memory:{title}"
        op = arguments.get("operation", "")
        if op == "list":
            # list 操作不针对特定文件
            return None
        return None

    return None


# ── Block 构建辅助 ────────────────────────────────────────────────────────


def _build_tool_call_block(tc: ToolCallDef, idx: int) -> Block:
    """构建 tool_call block（用于 UI 展示）"""
    # 参数摘要（与 loop.py 保持一致）
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

    return Block(
        block_type="tool_call",
        delta=f"{tc.name}{args_preview}",
        metadata={
            "args": tc.arguments,
            "index": idx,
            "tool_call_id": tc.id or f"call_{idx}",
        },
    )


def _build_tool_result_block(result: dict, idx: int) -> Block:
    """构建 tool_result block"""
    status = result.get("status", "?")
    # 提取结果摘要
    delta: str = ""
    if status == "ok":
        inner = result.get("result", {})
        if isinstance(inner, dict):
            delta = json_preview(inner, max_len=300)
        elif isinstance(inner, str):
            delta = inner[:300]
        else:
            delta = str(inner)[:300]
    elif status == "error":
        delta = f"错误: {result.get('error', 'unknown')}"
    elif status == "blocked":
        delta = f"被阻止: {result.get('reason', 'unknown')}"
    elif status == "needs_confirm":
        delta = f"需确认: {result.get('operation', 'unknown')}"
    else:
        delta = f"状态: {status}"

    return Block(
        block_type="tool_result",
        delta=delta,
        metadata={
            "status": status,
            "index": idx,
            "full_result": result,
        },
    )


def json_preview(obj: Any, max_len: int = 300) -> str:
    """将 Python 对象转为 JSON 字符串预览（截断超长内容）"""
    import json
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


# ── 写操作组执行 ────────────────────────────────────────────────────────────


class _WriteGroupScheduler:
    """写操作组调度器 — 文件级锁调度

    按资源路径分组后，同路径串行、不同路径并行。
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        tools: list[tuple[int, ToolCallDef]],
    ) -> list[dict]:
        """执行一组写操作。

        Args:
            tools: [(原始索引, ToolCallDef), ...] 列表

        Returns:
            与 tools 同顺序的执行结果列表
        """
        if not tools:
            return []

        # ── 1. 按资源路径分组 ──
        groups: dict[str | None, list[tuple[int, ToolCallDef]]] = {}
        for item in tools:
            idx, tc = item
            path = _resolve_resource_path(tc.name, tc.arguments)
            groups.setdefault(path, []).append(item)

        # ── 2. 每组内部串行，组间并行 ──
        async def _run_group(items: list[tuple[int, ToolCallDef]]) -> list[dict]:
            """同一资源路径下的写操作串行执行"""
            results: list[dict] = []
            for _idx, tc in items:
                try:
                    res = await self._registry.execute(tc.name, tc.arguments)
                except Exception as e:
                    res = {"status": "error", "error": str(e)}
                results.append(res)
            return results

        # 启动所有组（不同资源路径并行）
        group_tasks = [
            _run_group(items) for items in groups.values()
        ]

        group_results_list = await asyncio.gather(*group_tasks, return_exceptions=True)

        # ── 3. 按原始顺序重组结果 ──
        # 先构建 {原始索引 → 结果} 的映射
        index_to_result: dict[int, dict] = {}
        for group_items, group_results in zip(groups.values(), group_results_list):
            if isinstance(group_results, Exception):
                # 整个组异常 — 每个工具都标记为错误
                for idx, _tc in group_items:
                    index_to_result[idx] = {
                        "status": "error",
                        "error": f"写操作组执行失败: {group_results}",
                    }
            else:
                for (idx, _tc), res in zip(group_items, group_results):
                    index_to_result[idx] = res

        # 按 tools 传入顺序返回
        return [index_to_result[idx] for idx, _tc in tools]


# ── ParallelExecutor ────────────────────────────────────────────────────────


class ParallelExecutor:
    """并行工具调度器

    用法:
        executor = ParallelExecutor(registry)
        results = await executor.execute(tool_calls)
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._write_scheduler = _WriteGroupScheduler(registry)

    async def execute(
        self,
        tool_calls: list[ToolCallDef],
        send_block: Callable | None = None,
    ) -> list[dict]:
        """并行执行所有工具调用。

        返回与 tool_calls **同顺序**的结果列表（保持 tool_msg 顺序要求）。

        Args:
            tool_calls: LLM 返回的工具调用列表
            send_block: 推送 tool_call/tool_result block 的回调（来自 loop.py）

        Returns:
            与 tool_calls 长度相同的 [dict] 列表，每个元素对应 tool_calls[i] 的执行结果
        """
        n = len(tool_calls)
        # 预分配结果槽位
        results: list[dict | None] = [None] * n

        # ── Step 1: 分类 ──
        read_indices: list[int] = []
        write_indices: list[int] = []
        compound_indices: list[int] = []

        for i, tc in enumerate(tool_calls):
            category = _classify_tool(tc.name, tc.arguments)
            if category == "read":
                read_indices.append(i)
            elif category == "write":
                write_indices.append(i)
            else:
                compound_indices.append(i)

        logger.info(
            "[PARALLEL] 分类结果: %d read, %d write, %d compound (共 %d)",
            len(read_indices), len(write_indices), len(compound_indices), n,
        )

        # ── Step 2: 先推送所有 tool_call blocks ──
        # 让 UI 一次性展示所有工具（而非逐个展示）
        if send_block:
            for i, tc in enumerate(tool_calls):
                block = _build_tool_call_block(tc, i)
                await send_block(block)

        # ── Step 3: 读操作组 — 全并行 ──
        if read_indices:
            read_coros = [
                self._execute_single(tool_calls[i]) for i in read_indices
            ]
            read_results = await asyncio.gather(*read_coros, return_exceptions=True)

            for idx, res in zip(read_indices, read_results):
                if isinstance(res, Exception):
                    results[idx] = {
                        "status": "error",
                        "error": str(res),
                    }
                else:
                    results[idx] = res

            logger.info("[PARALLEL] 读操作组完成: %d/%d 成功",
                sum(1 for r in read_results if not isinstance(r, Exception) and r.get("status") == "ok"),
                len(read_indices))

        # ── Step 4: 写操作组 — 文件级锁调度 ──
        if write_indices:
            write_tools = [(i, tool_calls[i]) for i in write_indices]
            write_results = await self._write_scheduler.execute(write_tools)

            for idx, res in zip(write_indices, write_results):
                results[idx] = res

            logger.info("[PARALLEL] 写操作组完成: %d/%d 成功",
                sum(1 for r in write_results if isinstance(r, dict) and r.get("status") == "ok"),
                len(write_indices))

        # ── Step 5: 复合操作组 — 顺序串行 ──
        for i in compound_indices:
            try:
                results[i] = await self._execute_single(tool_calls[i])
            except Exception as e:
                results[i] = {"status": "error", "error": str(e)}

            logger.info("[PARALLEL] 复合操作 %s 完成: status=%s",
                tool_calls[i].name,
                results[i].get("status") if isinstance(results[i], dict) else "exception")

        # ── Step 6: 推送 tool_result blocks + 返回 ──
        if send_block:
            for i, result in enumerate(results):
                if result is not None:
                    block = _build_tool_result_block(result, i)
                    await send_block(block)

        # 确保所有槽位都有值（理论上不会发生，但防御性编程）
        final_results: list[dict] = [
            r if r is not None else {"status": "error", "error": "未执行"}
            for r in results
        ]

        return final_results

    async def _execute_single(self, tc: ToolCallDef) -> dict:
        """执行单个工具调用"""
        return await self._registry.execute(tc.name, tc.arguments)
