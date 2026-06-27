"""MemoryTool — 记忆操作打包工具 (add/read/search/list/del/edit/graph_search)"""

import asyncio
import logging
import re
from collections import deque
from datetime import datetime
from pathlib import Path

from core.memory.index import MemoryIndex
from core.tools.base import BaseTool

logger = logging.getLogger(__name__)

# 预加载 jieba 分词器，避免首次调用延迟
import jieba
jieba.initialize()

# 记忆存储目录
MEMORY_BASE = Path(__file__).parent.parent.parent / ".AIGEME" / ".data"


def _get_memory_dir(user_id: str = "local", char_id: str = "ario") -> Path:
    """获取记忆目录"""
    d = MEMORY_BASE / user_id / char_id / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


class MemoryTool(BaseTool):
    """记忆操作工具"""

    name = "memory"
    description = (
        "长期记忆操作。支持添加/读取/搜索/浏览/删除/编辑/链接/审计/合并/清理/图谱检索记忆。\n\n"
        "## 记忆行为规范\n\n"
        "你具备自主管理长期记忆的能力。请遵循以下规范：\n\n"
        "### 写入流程\n"
        "1. 当学到新信息时，直接调用 `add` 新增记忆。系统会自动检查是否已存在相似内容，\n"
        "   若相似度 >= 0.7 则会自动合并到已有文件，无需你手动查重。\n"
        "2. 更新已有记忆时，调用 `edit` 修改具体内容。编辑只在正文中生效，不影响元数据。\n"
        "3. `add` 时建议填写 `type`（记忆类型）和 `importance`（重要性 1-5）。\n\n"
        "### 关联与链接\n"
        "1. 你可以在记忆正文中使用 `[[文件名]]` 语法引用其他记忆文件，系统会自动建立双向链接。\n"
        "2. 需要显式关联两个已有记忆时，调用 `link` 操作。\n\n"
        "### 定期审计\n"
        "1. 建议每 15-20 轮对话调用一次 `audit` 检查记忆健康度。\n"
        "2. `audit` 会返回：断链列表、孤立文件、高相似度文件对。\n"
        "3. 根据 audit 结果：\n"
        "   - 断链 → 用 `link` 重新关联或忽略\n"
        "   - 孤立文件 → 用 `link` 补充关联或 `prune` 清理\n"
        "   - 高相似度文件 → 用 `merge` 合并\n"
        "4. 对于已合并的文件，原文件会自动归档到 `_archive/`。\n\n"
        "### 清理规则\n"
        "1. `del` 操作为软删除，文件移入 `_archive/` 而非物理删除。\n"
        "2. `prune` 清理完全孤立的记忆文件（无任何链接关系）。\n"
        "3. 核心文件（MEMORY.md、LINKS.md）不能被删除。"
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
        self._index_dirty = True  # ★ 新增：倒排索引标记脏

    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["add", "read", "search", "list", "del", "edit", "link", "audit", "merge", "prune", "graph_search", "task"],
                "description": "add=新增 / read=读取全文 / search=搜索关键词 / list=浏览索引 / del=删除文件 / edit=编辑文件 / link=建立链接 / audit=扫描审计 / merge=合并文件 / prune=清理孤立文件 / graph_search=图谱扩散检索",
            },
            "title": {
                "type": "string",
                "description": "记忆标题（文件名不含 .md）。add/read/del/edit 时必填",
            },
            "content": {
                "type": "string",
                "description": "记忆内容。add/edit 时使用；task add 时作为提醒内容（必填）",
            },
            "type": {
                "type": "string",
                "enum": ["fact", "preference", "task_status", "event", "emotion", "reflection", "process"],
                "description": "记忆类型。add 时必填。event=事件/fact=事实/process=过程/emotion=情感/reflection=反思",
            },
            "query": {
                "type": "string",
                "description": "搜索关键词。search 时必填",
            },
            "old_string": {
                "type": "string",
                "description": "被替换的原文。edit 时必填",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新内容 (edit 时必填，可传空字符串删除原文)",
            },
            "include_all": {
                "type": "boolean",
                "description": "list 时是否包含过期文件",
            },
            "importance": {
                "type": "integer",
                "description": "重要性 1-5 (add 时可选，默认 3)",
            },
            "src": {
                "type": "string",
                "description": "链接源文件（link 时必填）",
            },
            "tgt": {
                "type": "string",
                "description": "链接目标文件（link 时必填）",
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
            # ── 待办事项参数 ──
            "task_action": {
                "type": "string",
                "enum": ["add", "done", "cancel", "list", "read"],
                "description": "待办操作: add=新增 / done=完成 / cancel=取消 / list=列出 / read=读取详情",
            },
            "trigger_at": {
                "type": "string",
                "description": "触发时间 \"HH:MM\" 或 \"YYYY-MM-DD HH:MM\" 或 \"周3 10:00\"（task add 时必填）",
            },
            "repeat": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly"],
                "description": "重复模式: daily=每天 / weekly=每周 / monthly=每月（task add 可选）",
            },
            "id": {
                "type": "string",
                "description": "任务短ID（自动生成），task done/cancel/read 时必填",
            },
        },
        "required": ["operation"],
    }

    async def execute(  # type: ignore[override]
        self,
        operation: str,
        title: str | None = None,
        content: str | None = None,
        type: str | None = None,
        query: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        include_all: bool = False,
        importance: int = 3,
        src: str | None = None,
        tgt: str | None = None,
        sources: list[str] | None = None,
        target: str | None = None,
        seed: str | None = None,
        query_tags: list[str] | None = None,
        max_depth: int = 2,
        task_action: str | None = None,
        trigger_at: str | None = None,
        repeat: str | None = None,
        id: str | None = None,
        **kwargs,
    ) -> dict:
        memory_dir = _get_memory_dir(char_id=self._char_id)
        index = MemoryIndex(memory_dir)

        if operation == "add":
            if not title or not content or not type:
                return {
                    "status": "error",
                    "error": "add 操作需要 title, content, type 参数",
                }
            return await self._add_memory(memory_dir, index, title, content, type, importance)

        if operation == "read":
            if not title:
                return {"status": "error", "error": "read 操作需要 title 参数"}
            return await self._read_memory(memory_dir, index, title)

        if operation == "search":
            if not query:
                return {"status": "error", "error": "search 操作需要 query 参数"}
            return await self._search_memory(memory_dir, index, query)

        if operation == "list":
            return await self._list_memories(memory_dir, index, include_all)

        if operation == "del":
            if not title:
                return {"status": "error", "error": "del 操作需要 title 参数"}
            return await self._del_memory(memory_dir, index, title)

        if operation == "edit":
            if not title or not old_string:
                return {
                    "status": "error",
                    "error": "edit 操作需要 title, old_string 参数",
                }
            if new_string is None:
                new_string = ""
            return await self._edit_memory(memory_dir, index, title, old_string, new_string)

        # ── Brain Tools ───────────────────────────────────────

        if operation == "link":
            if not src or not tgt:
                return {"status": "error", "error": "link 操作需要 src 和 tgt 参数"}
            return await self._link_memory(memory_dir, index, src, tgt)

        if operation == "audit":
            return await self._audit_memory(memory_dir)

        if operation == "merge":
            if not sources or not target:
                return {"status": "error", "error": "merge 操作需要 sources 列表和 target 参数"}
            return await self._merge_memory(memory_dir, index, sources, target)

        if operation == "prune":
            return await self._prune_memory(memory_dir, index)

        # ── 待办事项 ──────────────────────────────────────────

        if operation == "task":
            from core.memory.reminder import TaskManager
            tm = TaskManager(memory_dir)
            if task_action == "add":
                _task_title = content or id or title or ""
                if not _task_title or not trigger_at:
                    return {"status": "error", "error": "task add 需要 content（提醒内容）和 trigger_at 参数"}
                return await tm.add(title=_task_title, trigger_at=trigger_at, content=_task_title, repeat=repeat)
            if task_action == "done":
                _task_id = id or title
                if not _task_id:
                    return {"status": "error", "error": "task done 需要 id（任务ID）参数"}
                return await tm.done(_task_id)
            if task_action == "cancel":
                _task_id = id or title
                if not _task_id:
                    return {"status": "error", "error": "task cancel 需要 id（任务ID）参数"}
                return await tm.cancel(_task_id)
            if task_action == "list":
                status_filter = kwargs.get("status", "")
                return await tm.list_tasks(status_filter)
            if task_action == "read":
                _task_id = id or title
                if not _task_id:
                    return {"status": "error", "error": "task read 需要 id（任务ID）参数"}
                return await tm.read_task(_task_id)
            return {"status": "error", "error": f"不支持的任务操作: {task_action}"}

        if operation == "graph_search":
            if not seed:
                return {"status": "error", "error": "graph_search 操作需要 seed 参数"}
            return await self._graph_search(memory_dir, index, seed, query_tags, max_depth)

        return {"status": "error", "error": f"不支持的操作: {operation}"}

    async def _add_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        title: str,
        content: str,
        type: str,
        importance: int,
    ) -> dict:
        """新增记忆

        ★ B4: 自动查重 — 如果发现相似内容 >= 0.7，追加到已有文件而非新建
        """
        # ★ B4: 自动查重
        similar = await self._check_similar_internal(memory_dir, content)
        if similar and similar["similarity"] >= 0.7:
            now = datetime.now()
            timestamp = now.strftime("%Y-%m-%d %H:%M")
            stars = "★" * importance + "☆" * (5 - importance)
            new_string = f"\n- [{timestamp}] [agent] [{type:<12}] {stars} {content}\n"
            return await self._append_to_existing(memory_dir, index, similar["file"], new_string)

        file_path = memory_dir / f"{title}.md"
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M")
        stars = "★" * importance + "☆" * (5 - importance)

        # 代码自动注入元数据
        entry = f"- [{timestamp}] [agent] [{type:<12}] {stars} {content}\n"

        # 文件写锁保护追加操作
        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire(file_path):
            if not file_path.exists():
                # ★ B1: 新文件 → 注入 YAML frontmatter
                from core.memory.yaml_handler import YamlFrontmatter
                fm_metadata = {"type": type, "source": "agent", "tags": []}
                full_content = YamlFrontmatter.inject(entry, fm_metadata)
                file_path.write_text(full_content, encoding="utf-8")
            else:
                # 已有文件 → 只追加条目（不改变 frontmatter）
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(entry)
                # 更新 frontmatter 中的 checksum 和 updated
                from core.memory.yaml_handler import YamlFrontmatter
                YamlFrontmatter.update(file_path, {})

        # 更新索引
        await index.update_after_add(
            title,
            {
                "entries": self._count_entries(file_path),
                "last_updated": now.strftime("%Y-%m-%d"),
                "last_referenced": now.strftime("%Y-%m-%d"),
                "summary": content[:30],
                "section": self._TYPE_TO_SECTION.get(type, "其他"),
            },
        )

        # 使 MEMORY.md 缓存失效
        self.invalidate_cache()

        # 诊断：验证 MEMORY.md 是否真的写入了新条目
        memory_md = memory_dir / "MEMORY.md"
        if memory_md.exists():
            md_content = memory_md.read_text("utf-8")
            if title in md_content:
                logger.info("[MEMORY_DEBUG] add → MEMORY.md 已包含 '%s' ✓", title)
            else:
                logger.warning("[MEMORY_DEBUG] add → MEMORY.md 未包含 '%s' ✗\n%s",
                    title, md_content[:500])
        else:
            logger.warning("[MEMORY_DEBUG] MEMORY.md 不存在！memory_dir=%s", memory_dir)

        return {"status": "ok", "result": {"file": f"{title}.md", "added": entry.strip()}}

    async def _read_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        title: str,
    ) -> dict:
        """读取记忆（自动更新引用时间）

        返回时剥离 YAML frontmatter，metadata 单独返回给 Agent。
        """
        file_path = memory_dir / f"{title}.md"
        if not file_path.exists():
            return {"status": "error", "error": f"记忆文件不存在: {title}.md"}

        # 文件读锁保护文件内容一致性
        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire_read(file_path):
            content = file_path.read_text("utf-8")

        # ★ B2: 剥离 YAML frontmatter，metadata 单独返回
        from core.memory.yaml_handler import YamlFrontmatter
        fm, body = YamlFrontmatter.extract_io(content)

        # 更新引用时间（MemoryIndex 内部的 RWLock 保护 MEMORY.md）
        await index.update_reference(title)

        result = {"file": f"{title}.md", "content": body.strip()}
        if fm:
            result["metadata"] = {
                "type": fm.get("type"),
                "created": fm.get("created"),
                "updated": fm.get("updated"),
                "tags": fm.get("tags", []),
                "links": fm.get("links", []),
                "source": fm.get("source"),
                "status": fm.get("status"),
            }
        return {"status": "ok", "result": result}

    async def _search_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        query: str,
    ) -> dict:
        """搜索记忆（跳过 >30 天未引用的文件，搜索不算引用）

        使用倒排索引将 O(n*m) 文件遍历 → O(1) 词查找 + O(k) 行读取
        """
        # 构建或更新倒排索引
        if self._index_dirty or self._inverted_index is None:
            self._inverted_index = await self._build_inverted_index(memory_dir)
            self._index_dirty = False

        # 分词查询
        query_words = self._tokenize(query)
        if not query_words:
            return {"status": "ok", "result": {"message": "没有找到匹配内容", "count": 0, "results": []}}

        # 取查询词的**并集**（任一匹配即加入）
        hit_files: dict[str, set[int]] = {}
        for word in query_words:
            word_hits = self._inverted_index.get(word, {})
            if not word_hits:
                continue
            for f, lns in word_hits.items():
                if f not in hit_files:
                    hit_files[f] = set()
                hit_files[f] |= set(lns)

        if not hit_files:
            return {"status": "ok", "result": {"message": "没有找到匹配内容", "count": 0, "results": []}}

        # 过滤 >30 天未引用的文件 + 读取匹配行
        results = []
        now = datetime.now()
        index_data = await index.parse()

        for fname, matched_lines in hit_files.items():
            file_stem = fname.replace(".md", "")
            last_ref = index_data.get(file_stem, {}).get("last_referenced", "")
            if last_ref and not self._is_within_days(last_ref, 30, now):
                continue

            # 只读取命中的行（完整文件只读一次）
            file_path = memory_dir / fname
            content = file_path.read_text("utf-8")
            all_lines = content.split("\n")
            matched = [
                all_lines[ln].strip()
                for ln in sorted(matched_lines)
                if ln < len(all_lines)
            ]

            if matched:
                results.append({
                    "file": fname,
                    "match_count": len(matched),
                    "preview": matched[0][:80],
                })

        return {"status": "ok", "result": {"count": len(results), "results": results}}

    async def _list_memories(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        include_all: bool,
    ) -> dict:
        """列出所有记忆主题"""
        index_content = memory_dir / "MEMORY.md"
        if not index_content.exists():
            return {"status": "ok", "result": {"index": ""}}

        content = index_content.read_text("utf-8")

        # 诊断：记录 list 返回的内容长度（不打印全量）
        entry_count = sum(1 for line in content.splitlines() if line.startswith("| ") and "|" in line[2:] and not line.strip().startswith("|---"))
        logger.info("[MEMORY_DEBUG] list → MEMORY.md 大小=%d 字节, 条目行数=%d",
            len(content), entry_count)

        if not include_all:
            # 过滤过期文件 + 已归档条目
            index_data = await index.parse()
            now = datetime.now()
            filtered_lines: list[str] = []
            for line in content.splitlines():
                if line.startswith("| ") and "|" in line[2:]:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 5 and parts[1]:
                        # 过滤过期文件
                        last_ref = index_data.get(parts[1], {}).get("last_referenced", "")
                        if last_ref and not self._is_within_days(last_ref, 30, now):
                            continue
                        # 过滤已归档条目
                        if index_data.get(parts[1], {}).get("status") == "archived":
                            continue
                filtered_lines.append(line)
            content = "\n".join(filtered_lines)

        return {"status": "ok", "result": {"index": content}}

    async def _del_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        title: str,
    ) -> dict:
        """删除记忆（软删除 → 移入 _archive/）

        不是物理删除，而是将文件移入 _archive/ 目录，
        并在 frontmatter 中标记 status: archived 和 archived_at 时间戳。
        """
        file_path = memory_dir / f"{title}.md"
        if not file_path.exists():
            return {"status": "error", "error": f"记忆文件不存在: {title}.md"}

        # 保护核心文件
        if title in ("MEMORY", "LINKS"):
            return {"status": "error", "error": f"核心文件 {title}.md 不允许删除"}

        # 检查是否已在 _archive/
        archive_dir = memory_dir / "_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        # 检查是否已归档（幂等）
        for existing in archive_dir.glob(f"{title}_*.md"):
            return {"status": "error", "error": f"{title}.md 已在 _archive/ 中，请勿重复归档"}

        # 在文件 frontmatter 中标记已归档
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = archive_dir / f"{title}_{timestamp}.md"

        try:
            content = file_path.read_text("utf-8")
            from core.memory.yaml_handler import YamlFrontmatter
            fm, body = YamlFrontmatter.extract_io(content)
            if fm:
                fm["status"] = "archived"
                fm["archived_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                import yaml
                fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
                archived_content = f"---\n{fm_str}\n---\n\n{body}"
                file_path.write_text(archived_content, encoding="utf-8")
        except Exception as e:
            logger.warning("归档标记 frontmatter 失败: %s", e)

        # 文件写锁保护移动操作
        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire(file_path):
            file_path.rename(dest)

        # 从索引中移除条目（归档文件的 frontmatter 已有 status: archived）
        await index.remove_entry(title)
        self.invalidate_cache()
        logger.info("记忆已归档: %s → _archive/%s_%s.md", title, title, timestamp)
        return {"status": "ok", "result": f"已归档 {title}.md → _archive/{title}_{timestamp}.md"}

    async def _edit_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        title: str,
        old_string: str,
        new_string: str,
    ) -> dict:
        """编辑记忆

        ★ B3: 保护 YAML frontmatter 不变，只修改正文
        """
        file_path = memory_dir / f"{title}.md"
        if not file_path.exists():
            return {"status": "error", "error": f"记忆文件不存在: {title}.md"}

        # 文件写锁保护读-改-写操作
        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire(file_path):
            content = file_path.read_text("utf-8")

            from core.memory.yaml_handler import YamlFrontmatter
            fm, body = YamlFrontmatter.extract_io(content)

            occurences = body.count(old_string)
            if occurences == 0:
                return {"status": "error", "error": "未找到匹配的原文"}
            if occurences > 1:
                return {"status": "error", "error": f"old_string 在文件中出现 {occurences} 次，请提供更精确的匹配文本以确保唯一性"}

            new_body = body.replace(old_string, new_string if new_string else "")

            # 重建文件内容（保留 frontmatter）
            if fm:
                fm["updated"] = YamlFrontmatter._now_str()
                fm["checksum"] = YamlFrontmatter._checksum(new_body)
                import yaml
                fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
                new_content = f"---\n{fm_str}\n---\n\n{new_body}\n"
            else:
                new_content = new_body

            file_path.write_text(new_content, encoding="utf-8")

        await index.update_modify(title, memory_dir)
        self.invalidate_cache()
        return {"status": "ok", "result": f"已编辑 {title}.md"}

    # ── Graph Search (图谱扩散检索) ─────────────────────────

    async def _graph_search(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        seed: str,
        query_tags: list[str] | None = None,
        max_depth: int = 2,
    ) -> dict:
        """图谱扩散检索调度

        优先走 BFS 图谱扩散（_graph_search_memory），
        如果 seed 不在图谱中，降级为标签匹配（_tag_search）。
        """
        from core.memory.link_graph import LinkGraph

        lg = LinkGraph(memory_dir)
        await lg.initialize()
        graph = await lg.parse_links()
        forward = graph.get("forward", {})

        # 检查 seed 是否在图谱中
        if seed in forward or any(seed in targets for targets in forward.values()):
            results = await self._graph_search_memory(
                memory_dir, seed, query_tags, max_depth,
            )
        else:
            # seed 不在图谱中，降级为标签搜索
            if query_tags:
                results = await self._tag_search(memory_dir, query_tags)
            else:
                results = []

        return {
            "status": "ok",
            "result": ({
                "message": "没有找到匹配内容",
                "count": 0,
                "results": [],
                "method": "graph_search" if (
                    seed in forward or any(seed in targets for targets in forward.values())
                ) else "tag_fallback",
            } if not results else {
                "count": len(results),
                "results": results,
                "method": "graph_search" if (
                    seed in forward or any(seed in targets for targets in forward.values())
                ) else "tag_fallback",
            }),
        }

    async def _graph_search_memory(
        self,
        memory_dir: Path,
        seed: str,
        query_tags: list[str] | None = None,
        max_depth: int = 2,
        max_results: int = 10,
    ) -> list[dict]:
        """BFS 图谱扩散检索

        从 seed 节点出发，通过 LINKS.md 中的双向链接关系进行扩散，
        找到关联记忆并按相关性排序。

        信号融合：
        - A: 图谱扩散 — BFS 遍历，深度越浅权重越高
        - B: 标签命中 — query_tags 与 frontmatter tags 匹配加分
        - D: 时间衰减 — 超过 30 天未引用的文件降权但不排除
        """
        from core.memory.link_graph import LinkGraph
        from core.memory.yaml_handler import YamlFrontmatter

        lg = LinkGraph(memory_dir)
        await lg.initialize()
        graph = await lg.parse_links()

        forward = graph.get("forward", {})  # source -> {targets}

        # BFS 从 seed 扩散
        visited: set[str] = {seed}
        queue: deque[tuple[str, int, list[str]]] = deque([(seed, 0, [seed])])
        results: list[dict] = []
        now = datetime.now()

        while queue and len(results) < max_results:
            node, depth, path = queue.popleft()

            if depth > 0:  # seed 自身不加入结果
                file_path = memory_dir / f"{node}.md"
                if file_path.exists():
                    content = file_path.read_text("utf-8")
                    fm, body = YamlFrontmatter.extract_io(content)

                    # 信号 A: 图谱扩散 — 深度越浅权重越高
                    relevance = 1.0 - (depth * 0.2)

                    # 信号 B: 标签命中加分
                    if query_tags and fm:
                        file_tags = set(fm.get("tags", []))
                        matched_tags = file_tags & set(query_tags)
                        if matched_tags:
                            relevance += 0.1 * len(matched_tags)

                    # 信号 D: 时间衰减降权（不排除）
                    if fm:
                        updated = fm.get("updated", "")
                        if updated and not self._is_within_days(updated, 30, now):
                            relevance *= 0.8

                    # 取正文前 100 字作为预览
                    preview = body.strip()[:100] if body.strip() else "(空)"

                    results.append({
                        "file": f"{node}.md",
                        "depth": depth,
                        "path": " -> ".join(f"[[{p}]]" for p in path),
                        "relevance": round(relevance, 2),
                        "preview": preview,
                        "tags": fm.get("tags", []) if fm else [],
                        "type": fm.get("type", "") if fm else "",
                    })

            if depth < max_depth:
                neighbors = forward.get(node, set())
                for nb in sorted(neighbors):
                    if nb not in visited:
                        visited.add(nb)
                        queue.append((nb, depth + 1, path + [nb]))

        # 按相关性排序
        results.sort(key=lambda r: r["relevance"], reverse=True)

        return results[:max_results]

    async def _tag_search(
        self,
        memory_dir: Path,
        query_tags: list[str],
        max_results: int = 10,
    ) -> list[dict]:
        """按标签搜索 — 遍历所有文件匹配 tags

        当 seed 不在图谱中时降级使用（信号 B 兜底）。
        """
        from core.memory.yaml_handler import YamlFrontmatter

        results: list[dict] = []
        for f in sorted(memory_dir.glob("*.md")):
            if f.name in ("MEMORY.md", "LINKS.md"):
                continue
            content = f.read_text("utf-8")
            fm, _ = YamlFrontmatter.extract_io(content)
            if fm and query_tags:
                file_tags = set(fm.get("tags", []))
                matched = file_tags & set(query_tags)
                if matched:
                    body = f.read_text("utf-8")
                    _, body = YamlFrontmatter.extract_io(body)
                    results.append({
                        "file": f.name,
                        "depth": 0,
                        "path": "(标签匹配)",
                        "relevance": 0.5 + 0.1 * len(matched),
                        "preview": body.strip()[:100] if body.strip() else "(空)",
                        "tags": fm.get("tags", []),
                        "type": fm.get("type", ""),
                    })
        results.sort(key=lambda r: r["relevance"], reverse=True)
        return results[:max_results]

    # ── B4: 查重 ───────────────────────────────────────────────

    async def _check_similar_internal(self, memory_dir: Path, content: str) -> dict | None:
        """系统内部查重（add 时自动调用）

        使用词重叠率（Jaccard 相似度）进行查重，如果与已有文件的重叠率 >= 0.7，返回匹配文件信息。

        Args:
            memory_dir: 记忆目录
            content: 要检查的内容（原始内容，不含时间戳和 importance）

        Returns:
            {"file": str, "similarity": float} 或 None
        """
        words = self._tokenize(content)
        if not words:
            return None

        # 搜索内容相似度
        if self._index_dirty or self._inverted_index is None:
            self._inverted_index = await self._build_inverted_index(memory_dir)
            self._index_dirty = False

        hit_files: dict[str, set[int]] | None = None
        for word in words:
            word_hits = self._inverted_index.get(word, {})
            if not word_hits:
                continue
            if hit_files is None:
                hit_files = {f: set(lns) for f, lns in word_hits.items()}
            else:
                hit_files = {f: hit_files[f] & word_hits[f] for f in hit_files if f in word_hits}

        if hit_files:
            best_file = max(hit_files, key=lambda f: len(hit_files[f]))
            overlap = len(hit_files[best_file])
            total_words = len(words)
            similarity = overlap / max(total_words, 1)
            if similarity >= 0.7:
                return {"file": best_file, "similarity": similarity}

        return None

    async def _append_to_existing(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        filename: str,
        new_string: str,
    ) -> dict:
        """向已存在的记忆文件追加条目（查重命中后使用）"""
        file_path = memory_dir / filename
        now = datetime.now()

        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire(file_path):
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(new_string)
            # 更新 frontmatter 的 checksum 和 updated
            from core.memory.yaml_handler import YamlFrontmatter
            YamlFrontmatter.update(file_path, {})

        title = filename.replace(".md", "")
        await index.update_after_add(
            title,
            {
                "entries": self._count_entries(file_path),
                "last_updated": now.strftime("%Y-%m-%d"),
                "last_referenced": now.strftime("%Y-%m-%d"),
                "summary": new_string.strip()[:30],
                "section": "其他",
            },
        )

        self.invalidate_cache()
        return {"status": "ok", "result": {"file": filename, "added": new_string.strip()}}

    # ── Brain Tools ───────────────────────────────────────────

    async def _link_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        src: str,
        tgt: str,
    ) -> dict:
        """建立两个记忆文件之间的双向链接

        在 src 和 tgt 文件的 YAML frontmatter 的 links 字段中互相添加对方，
        并更新 LINKS.md 图谱。
        """
        src_path = memory_dir / f"{src}.md"
        tgt_path = memory_dir / f"{tgt}.md"

        if not src_path.exists():
            return {"status": "error", "error": f"源文件不存在: {src}.md"}
        if not tgt_path.exists():
            return {"status": "error", "error": f"目标文件不存在: {tgt}.md"}

        from core.memory.link_graph import LinkGraph
        from core.memory.yaml_handler import YamlFrontmatter

        lg = LinkGraph(memory_dir)

        # 在 src 的 frontmatter links 中添加 tgt
        src_fm, _ = YamlFrontmatter.extract(src_path)
        existing_src_links = src_fm.get("links", [])
        if not isinstance(existing_src_links, list):
            existing_src_links = []
        tgt_link = f"[[{tgt}]]"
        if tgt_link not in existing_src_links:
            existing_src_links.append(tgt_link)
            YamlFrontmatter.update(src_path, {"links": existing_src_links})

        # 在 tgt 的 frontmatter links 中添加 src
        tgt_fm, _ = YamlFrontmatter.extract(tgt_path)
        existing_tgt_links = tgt_fm.get("links", [])
        if not isinstance(existing_tgt_links, list):
            existing_tgt_links = []
        src_link = f"[[{src}]]"
        if src_link not in existing_tgt_links:
            existing_tgt_links.append(src_link)
            YamlFrontmatter.update(tgt_path, {"links": existing_tgt_links})

        # 更新 LINKS.md 图谱
        await lg.update_links(src_path)
        await lg.update_links(tgt_path)

        logger.info("已建立双向链接: %s ↔ %s", src, tgt)
        return {"status": "ok", "result": f"已建立链接: {src} ↔ {tgt}"}

    async def _audit_memory(
        self,
        memory_dir: Path,
    ) -> dict:
        """扫描审计记忆库

        执行三项扫描：
        1. 断链检测 — LINKS.md 记录的链接目标文件已不存在
        2. 孤立文件检测 — 无入链无出链的文件
        3. 总文件数统计
        """
        from core.memory.link_graph import LinkGraph
        lg = LinkGraph(memory_dir)
        await lg.initialize()

        dead_links = await lg.detect_dead_links()
        orphans = await lg.find_orphans()

        total_files = len([
            f for f in memory_dir.glob("*.md")
            if f.name not in ("MEMORY.md", "LINKS.md")
        ])

        # 简单的文件对相似度检测
        similar_pairs = await self._check_similar_pairs(memory_dir)

        return {
            "status": "ok",
            "result": {
                "dead_links": dead_links,
                "orphans": orphans,
                "similar_pairs": similar_pairs,
                "total_files": total_files,
            },
        }

    async def _merge_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        sources: list[str],
        target: str,
    ) -> dict:
        """合并多个记忆文件到一个目标文件

        1. 读取所有源文件内容
        2. 合并 frontmatter（created 取最早的，updated 为当前时间，tags 取并集，生成新 id）
        3. 合并正文内容到 target
        4. 源文件移入 _archive/
        """
        if not sources:
            return {"status": "error", "error": "sources 不能为空"}

        from core.memory.yaml_handler import YamlFrontmatter
        from core.memory.link_graph import LinkGraph

        # 验证所有源文件都存在
        source_paths = []
        for src in sources:
            sp = memory_dir / f"{src}.md"
            if not sp.exists():
                return {"status": "error", "error": f"源文件不存在: {src}.md"}
            source_paths.append(sp)

        # 读取所有源文件的内容
        merged_bodies: list[str] = []
        earliest_created: str | None = None
        merged_tags: set[str] = set()
        merged_type: str = "fact"

        for sp in source_paths:
            content = sp.read_text("utf-8")
            fm, body = YamlFrontmatter.extract_io(content)
            merged_bodies.append(body.strip())

            # 取最早的 created
            created = fm.get("created", "")
            if created and (earliest_created is None or created < earliest_created):
                earliest_created = created

            # tags 取并集
            tags = fm.get("tags", [])
            if isinstance(tags, list):
                for t in tags:
                    if isinstance(t, str):
                        merged_tags.add(t)

            # type 取第一个非空的
            if not merged_type or merged_type == "fact":
                merged_type = fm.get("type", "fact")

        merged_body_text = "\n\n---\n\n".join(merged_bodies)

        # 写入/创建 target 文件
        target_path = memory_dir / f"{target}.md"

        if target_path.exists():
            # 目标已存在 → 追加内容并更新 frontmatter
            existing_content = target_path.read_text("utf-8")
            tgt_fm, tgt_body = YamlFrontmatter.extract_io(existing_content)
            new_body = tgt_body.strip() + "\n\n---\n\n" + merged_body_text
            tgt_fm["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tgt_fm["checksum"] = YamlFrontmatter._checksum(new_body)
            # tags 取并集
            existing_tags = set()
            existing_tags_list = tgt_fm.get("tags", [])
            if isinstance(existing_tags_list, list):
                for t in existing_tags_list:
                    if isinstance(t, str):
                        existing_tags.add(t)
            all_tags = sorted(existing_tags | merged_tags)
            tgt_fm["tags"] = all_tags
            import yaml
            fm_str = yaml.dump(tgt_fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
            target_path.write_text(f"---\n{fm_str}\n---\n\n{new_body}\n", encoding="utf-8")
        else:
            # 目标不存在 → 创建新文件
            fm_metadata = {
                "type": merged_type,
                "source": "agent",
                "tags": sorted(merged_tags),
                "links": [],
            }
            full_content = YamlFrontmatter.inject(merged_body_text, fm_metadata)
            # 覆盖 created 为最早的
            full_fm, _ = YamlFrontmatter.extract_io(full_content)
            if earliest_created and full_fm:
                full_fm["created"] = earliest_created
                import yaml
                fm_str = yaml.dump(full_fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
                full_content = f"---\n{fm_str}\n---\n\n{merged_body_text}\n"
            target_path.write_text(full_content, encoding="utf-8")

        # 源文件移入 _archive/
        archive_dir = memory_dir / "_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        lg = LinkGraph(memory_dir)
        archived_sources = []
        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()

        for sp in source_paths:
            stem = sp.stem
            # 更新 LINKS.md 前先读取，完成后归档
            await lg.update_links(sp)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = archive_dir / f"{stem}_{timestamp}.md"
            async with lm.acquire(sp):
                sp.rename(dest)
            await index.remove_entry(stem)
            archived_sources.append(f"{stem}.md")

        # 更新目标文件的 LINKS.md
        await lg.update_links(target_path)

        # 更新索引中的 target
        now = datetime.now()
        await index.update_after_add(
            target,
            {
                "entries": self._count_entries(target_path),
                "last_updated": now.strftime("%Y-%m-%d"),
                "last_referenced": now.strftime("%Y-%m-%d"),
                "summary": merged_body_text[:30],
                "section": "其他",
            },
        )

        self.invalidate_cache()
        logger.info("已合并 %d 个文件 → %s.md, 已归档: %s",
                     len(sources), target, archived_sources)
        return {
            "status": "ok",
            "result": {
                "target": f"{target}.md",
                "archived_sources": archived_sources,
                "merged_count": len(sources),
            },
        }

    async def _prune_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
    ) -> dict:
        """清理孤立/过期文件，移入 _archive/

        将孤立文件（无入链无出链）移入 _archive/，排除 MEMORY.md 和 LINKS.md。
        """
        from core.memory.link_graph import LinkGraph
        lg = LinkGraph(memory_dir)
        await lg.initialize()

        orphans = await lg.find_orphans()
        archive_dir = memory_dir / "_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        pruned: list[str] = []
        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()

        for fname in orphans:
            src_path = memory_dir / fname
            if not src_path.exists():
                continue
            stem = src_path.stem
            # 保护核心文件
            if stem in ("MEMORY", "LINKS"):
                continue
            # 检查是否已归档
            already_archived = False
            for existing in archive_dir.glob(f"{stem}_*.md"):
                already_archived = True
                break
            if already_archived:
                continue

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = archive_dir / f"{stem}_{timestamp}.md"

            # 归档标记 frontmatter
            try:
                content = src_path.read_text("utf-8")
                from core.memory.yaml_handler import YamlFrontmatter
                fm, body = YamlFrontmatter.extract_io(content)
                if fm:
                    fm["status"] = "archived"
                    fm["archived_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    import yaml
                    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
                    archived_content = f"---\n{fm_str}\n---\n\n{body}"
                    src_path.write_text(archived_content, encoding="utf-8")
            except Exception as e:
                logger.warning("prune 标记 frontmatter 失败 %s: %s", fname, e)

            async with lm.acquire(src_path):
                src_path.rename(dest)
            await index.remove_entry(stem)
            pruned.append(fname)
            logger.info("prune 已归档: %s", fname)

        self.invalidate_cache()
        return {"status": "ok", "result": {"pruned": pruned}}

    async def _check_similar_pairs(self, memory_dir: Path) -> list[dict]:
        """扫描所有文件对，检测高词重叠率文件（辅助 audit 使用）

        对每对文件计算 Jaccard 词重叠率，返回 >= 0.5 的重叠对。
        """
        files = list(memory_dir.glob("*.md"))
        files = [f for f in files if f.name not in ("MEMORY.md", "LINKS.md")]
        if len(files) < 2:
            return []

        # 预读取所有文件内容
        file_contents: dict[str, tuple[set[str], str]] = {}
        for f in files:
            try:
                from core.memory.yaml_handler import YamlFrontmatter
                content = f.read_text("utf-8")
                _, body = YamlFrontmatter.extract_io(content)
                words = self._tokenize(body)
                file_contents[f.stem] = (words, body[:60])
            except Exception:
                continue

        results: list[dict] = []
        names = list(file_contents.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a_words, a_preview = file_contents[names[i]]
                b_words, b_preview = file_contents[names[j]]
                if not a_words or not b_words:
                    continue
                overlap = len(a_words & b_words)
                similarity = overlap / max(len(a_words | b_words), 1)
                if similarity >= 0.5:
                    results.append({
                        "file_a": f"{names[i]}.md",
                        "file_b": f"{names[j]}.md",
                        "similarity": round(similarity, 3),
                        "preview_a": a_preview,
                        "preview_b": b_preview,
                    })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:10]  # 最多返回 Top-10

    @staticmethod
    def _count_entries(file_path: Path) -> str:
        """统计文件中的条目数"""
        try:
            count = sum(1 for line in file_path.read_text("utf-8").splitlines() if line.startswith("- ["))
            return str(count)
        except Exception:
            return "0"

    @staticmethod
    def _is_within_days(date_str: str, days: int, now: datetime) -> bool:
        """检查日期是否在指定天数内"""
        try:
            d = datetime.strptime(date_str[:10], "%Y-%m-%d") if date_str else now
            return (now - d).days <= days
        except (ValueError, IndexError):
            return True

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """中文分词：使用 jieba 提取有意义的词汇
        
        英文单词保持原有逻辑（提取 >=2 字符的单词），
        中文部分改用 jieba 分词，避免产生无意义双字。
        """
        words: set[str] = set()
        # 英文词
        for m in re.finditer(r'[a-zA-Z_]\w{1,}', text):
            words.add(m.group().lower())
        # 中文部分使用 jieba 分词
        chinese_text = re.sub(r'[^\u4e00-\u9fff]', '', text)
        if chinese_text:
            for word in jieba.lcut(chinese_text):
                w = word.strip()
                if len(w) >= 2:
                    words.add(w)
        return words

    async def _build_inverted_index(self, memory_dir: Path) -> dict:
        """构建 {word: {filename: {line_indices}}} 倒排索引

        只索引以 "- [" 开头的记忆行（跳过 MEMORY.md 等元数据文件），逐文件加读锁
        """
        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        index: dict[str, dict[str, set[int]]] = {}
        for f in sorted(memory_dir.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            async with lm.acquire_read(f):
                content = f.read_text("utf-8")
            for ln, line in enumerate(content.split("\n")):
                if not line.startswith("- ["):
                    continue
                words = self._tokenize(line)
                for word in words:
                    if word not in index:
                        index[word] = {}
                    if f.name not in index[word]:
                        index[word][f.name] = set()
                    index[word][f.name].add(ln)
        return index
