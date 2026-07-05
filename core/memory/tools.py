"""MemoryTool — 记忆操作打包工具 (add/read/search/list/del/link/audit/merge/prune/graph_search)

拆分说明：具体操作实现在 core/memory/ops/ 中各 mixin 文件中。
本文件仅保留：状态管理、execute 调度、模块级辅助函数。
"""

import asyncio
import logging
import random
from datetime import datetime
from pathlib import Path

from core.memory.index import MemoryIndex
from core.memory.yaml_handler import YamlFrontmatter
from core.memory.ops import (
    MemoryCrudMixin,
    MemoryGraphMixin,
    MemoryMergeMixin,
    MemorySearchMixin,
    MemoryUtilsMixin,
)
from core.tools.base import BaseTool

logger = logging.getLogger(__name__)

# 记忆存储目录
MEMORY_BASE = Path(__file__).parent.parent.parent / ".AIGEME" / ".data"


def _resolve_memory_file(memory_dir: Path, mem_id: str) -> Path | None:
    """按 id（即文件名）查找记忆文件"""
    direct = memory_dir / f"{mem_id}.md"
    return direct if direct.exists() else None


def _get_memory_dir(user_id: str = "local", char_id: str = "ario") -> Path:
    """获取记忆目录"""
    d = MEMORY_BASE / user_id / char_id / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


class MemoryTool(
    MemoryGraphMixin,
    MemoryMergeMixin,
    MemoryCrudMixin,
    MemorySearchMixin,
    MemoryUtilsMixin,
    BaseTool,
):
    """记忆操作工具"""

    name = "memory"
    description = (
        "长期记忆操作：add/read/search/list/del/link/"
        "audit/merge/prune/graph_search 共 10 种。"
        "编辑功能已拆分为独立工具（memory_edit_content / memory_edit_title 等），"
        "详细规范请用 skill(operation=\"use\", name=\"memory-management-guide\") 查看完整指南。"
    )

    # 类型 → 分区映射表
    _TYPE_TO_SECTION = {
        "event": "事件记忆",
        "fact": "事实记忆",
        "process": "过程记忆",
        "emotion": "情感记忆",
        "reflection": "反思记忆",
        "preference": "事实记忆",
        "task_status": "过程记忆",
    }

    def __init__(self) -> None:
        """实例级 MEMORY.md 索引缓存，带 asyncio.Lock 保护"""
        super().__init__()
        self._char_id: str = "ario"  # 默认角色，连接建立时由 ws_server 覆盖
        self._index_cache: str = ""
        self._cache_dirty: bool = True
        self._cache_lock: asyncio.Lock = asyncio.Lock()
        self._inverted_index: dict[str, dict[str, set[int]]] | None = None
        self._built_index_version: int = 0
        self._index_dirty: bool = True

    def set_char_id(self, char_id: str) -> None:
        """由 ws_server 在连接建立时设置当前角色 ID"""
        self._char_id = char_id
        self.invalidate_cache()

    async def get_index_text(self, memory_dir: Path) -> str:
        """获取缓存的 MEMORY.md 内容，仅脏时重新读取磁盘（线程安全）"""
        async with self._cache_lock:
            if self._cache_dirty or not self._index_cache:
                index_file = memory_dir / "MEMORY.md"
                if index_file.exists():
                    self._index_cache = index_file.read_text("utf-8")
                else:
                    self._index_cache = ""
                self._cache_dirty = False
            return self._index_cache

    def invalidate_cache(self) -> None:
        """使缓存失效，下次 get_index_text 时重新从磁盘加载"""
        self._cache_dirty = True
        self._inverted_index = None
        self._index_dirty = True

    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "add",
                    "read",
                    "search",
                    "list",
                    "del",
                    "link",
                    "audit",
                    "merge",
                    "prune",
                    "graph_search",
                ],
                "description": (
                    "add=新增 / read=读取全文 / search=搜索关键词 / "
                    "list=浏览索引 / del=删除文件 / "
                    "link=建立链接 / audit=扫描审计 / merge=合并文件 / "
                    "prune=清理孤立文件 / graph_search=图谱扩散检索。\n"
                    "内容的增删改请用: add / del / memory_edit_content / "
                    "memory_edit_tags / memory_edit_title / memory_edit_importance。"
                ),
            },
            "content": {
                "type": "string",
                "description": "记忆内容。add 时使用",
            },
            "type": {
                "type": "string",
                "enum": [
                    "fact",
                    "preference",
                    "task_status",
                    "event",
                    "emotion",
                    "reflection",
                    "process",
                ],
                "description": (
                    "记忆类型。add 时可选（默认 fact）。"
                    "event=事件/fact=事实/process=过程/emotion=情感/reflection=反思"
                ),
            },
            "query": {
                "type": "string",
                "description": "搜索关键词。search 时必填",
            },
            "include_all": {
                "type": "boolean",
                "description": "list 时是否包含过期文件",
            },
            "importance": {
                "type": "integer",
                "description": (
                    "文件级重要度 1-5。新文件 add 时必须传；追加到已有文件时请忽略此参数（系统不会更新）。\n"
                    "1=临时 / 2=低价值 / 3=普通 / 4=重要 / 5=核心永久"
                ),
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "标签列表。add 时可选，给新增记忆附加标签。",
            },
            "tags_filter": {
                "type": "array",
                "items": {"type": "string"},
                "description": "标签筛选列表。search 时可选，只返回包含指定标签的记忆结果",
            },
            "src": {
                "type": "string",
                "description": "链接源文件名（link 必填，不含 .md 后缀），如 '出差记录' 而非 '出差记录.md'",
            },
            "tgt": {
                "type": "string",
                "description": "链接目标文件名（link 必填，不含 .md 后缀）",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "待合并的源文件列表（merge 时必填）",
            },
            "target": {
                "type": "string",
                "description": "合并目标文件（merge 时必填）",
            },
            "seed": {
                "type": "string",
                "description": "起始节点文件名（graph_search 时必填，不含 .md）",
            },
            "query_tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选的标签过滤列表（graph_search）",
            },
            "max_depth": {
                "type": "integer",
                "description": "图谱扩散最大深度（graph_search 可选，默认 2）",
            },
            "title": {
                "type": "string",
                "description": (
                    "记忆标题（add 时可选，存入 frontmatter 显示用，同标题自动追加到同一文件）；"
                    "read/del 时可选（与 id 二选一，按标题查找）"
                ),
            },
            "id": {
                "type": "string",
                "description": (
                    "记忆唯一标识（自动生成，8位时间码+随机数）。"
                    "read/del 时可选（与 title 二选一，优先精确匹配）"
                ),
            },
        },
        "required": ["operation"],
    }

    async def execute(  # type: ignore[override]
        self,
        operation: str,
        content: str | None = None,
        type: str | None = None,
        query: str | None = None,
        include_all: bool = False,
        importance: int = 3,
        tags: list[str] | None = None,
        tags_filter: list[str] | None = None,
        src: str | None = None,
        tgt: str | None = None,
        sources: list[str] | None = None,
        target: str | None = None,
        seed: str | None = None,
        query_tags: list[str] | None = None,
        max_depth: int = 2,
        id: str | None = None,
        title: str | None = None,
        **kwargs,
    ) -> dict:
        memory_dir = _get_memory_dir(char_id=self._char_id)
        index = MemoryIndex(memory_dir)
        _tags = tags or []

        # 剥离 .md 后缀（LLM 可能传 test.md 而非 test）
        if title and title.endswith(".md"):
            title = title[:-3]
        if id and id.endswith(".md"):
            id = id[:-3]

        # ── add: id=自动文件名，title=显示标题存 frontmatter ──
        if operation == "add":
            if not content:
                return {"status": "error", "error": "add 操作需要 content（内容）参数"}
            _type = type or "fact"
            _id: str
            if title:
                _id = YamlFrontmatter.sanitize_filename(title)
            else:
                _id = datetime.now().strftime("%y%m%d%H%M%S") + str(random.randint(10, 99))

            # 第一步：内容相似度查重（不依赖是否有 title）
            similar = await self._check_similar_internal(memory_dir, content)
            if similar and similar["similarity"] >= 0.7:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                new_string = f"\n- [{ts}] [agent] [{_type}] {content}\n"
                return await self._append_to_existing(
                    memory_dir, index, similar["file"], new_string, _tags
                )

            # 第二步：相似度未命中 → title 路由
            if title:
                existing = await self._find_by_title(memory_dir, title)
                if not existing:
                    existing = _resolve_memory_file(memory_dir, title)
                    if existing:
                        existing = existing.stem
                if existing:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                    new_string = f"\n- [{ts}] [agent] [{_type}] {content}\n"
                    return await self._append_to_existing(
                        memory_dir, index, existing, new_string, _tags
                    )

            # 第三步：都不命中 → 创建新文件（不传 importance 时默认 3）
            return await self._add_memory(
                memory_dir,
                index,
                _id,
                content,
                _type,
                importance,
                _tags,
                display_title=title,
            )

        # ── 统一查找：id（文件名精确）or title（frontmatter 搜索）──
        _lookup = None
        if id:
            _f = _resolve_memory_file(memory_dir, id)
            if _f:
                _lookup = _f.stem
        if not _lookup and title:
            _f = await self._find_by_title(memory_dir, title)
            if _f:
                # _find_by_title 已返回 stem（不含 .md 的字符串），直接使用
                _lookup = _f
            else:
                # 回退：按文件名匹配（兼容 title=文件名 的老记忆）
                _f = _resolve_memory_file(memory_dir, title)
                if _f:
                    _lookup = _f.stem

        if operation == "read":
            if not _lookup:
                return {"status": "error", "error": "read 需要 id 或 title 参数"}
            return await self._read_memory(memory_dir, index, _lookup)

        if operation == "search":
            if not query:
                return {"status": "error", "error": "search 操作需要 query 参数"}
            return await self._search_memory(memory_dir, index, query, tags_filter)

        if operation == "list":
            return await self._list_memories(memory_dir, index, include_all)

        if operation == "del":
            if not _lookup:
                return {"status": "error", "error": "del 需要 id 或 title 参数"}
            return await self._del_memory(memory_dir, index, _lookup)

        # ── Brain Tools ───────────────────────────────────────

        if operation == "link":
            if not src or not tgt:
                return {"status": "error", "error": "link 操作需要 src 和 tgt 参数"}
            return await self._link_memory(memory_dir, index, src, tgt)

        if operation == "audit":
            return await self._audit_memory(memory_dir)

        if operation == "merge":
            if not sources or not target:
                return {
                    "status": "error",
                    "error": "merge 操作需要 sources 列表和 target 参数",
                }
            return await self._merge_memory(memory_dir, index, sources, target)

        if operation == "prune":
            return await self._prune_memory(memory_dir, index)

        if operation == "graph_search":
            if not seed:
                return {
                    "status": "error",
                    "error": "graph_search 操作需要 seed 参数",
                }
            return await self._graph_search(
                memory_dir, index, seed, query_tags, max_depth
            )

        return {"status": "error", "error": f"不支持的操作: {operation}"}
