"""记忆编辑工具集 — 4 个独立工具，每个定义自己的必填参数

拆分目标：原 MemoryTool 的 ``edit`` 操作将所有参数设为可选，
导致小参数模型频繁填错参数组合。拆分后每个工具只有自己的必填字段，
减少选择冲突。

| 工具名 | 功能 | 必填参数 |
|--------|------|---------|
| memory_edit_content | 编辑正文（字符串替换） | title + old_string |
| memory_edit_tags | 替换标签列表 | title + tags |
| memory_edit_title | 编辑显示标题（frontmatter） | title + new_title |
| memory_edit_importance | 编辑重要度（1-5） | title + new_importance |

每个工具都以 ``title`` 作为标识要编辑的记忆文件（按 frontmatter title 或文件名匹配）。
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from core.memory.index import MemoryIndex
from core.memory.link_graph import LinkGraph
from core.memory.yaml_handler import YamlFrontmatter
from core.tools.base import BaseTool
from core.tools.file_lock import LockManager

logger = logging.getLogger(__name__)

# 记忆存储目录（与 MemoryTool 共享）
MEMORY_BASE = Path(__file__).parent.parent.parent / ".AIGEME" / ".data"


def _get_memory_dir(user_id: str = "local", char_id: str = "ario") -> Path:
    """获取记忆目录"""
    d = MEMORY_BASE / user_id / char_id / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_memory_file(memory_dir: Path, mem_id: str) -> Path | None:
    """按 id（即文件名）查找记忆文件"""
    direct = memory_dir / f"{mem_id}.md"
    return direct if direct.exists() else None


async def _find_by_title(memory_dir: Path, title: str) -> str | None:
    """搜索所有记忆文件的 frontmatter，返回第一个 title 匹配的文件名（不含 .md）"""
    if not memory_dir.exists():
        return None
    for fname in os.listdir(memory_dir):
        if not fname.endswith(".md"):
            continue
        if fname in ("MEMORY.md", "LINKS.md"):
            continue
        try:
            fm, _ = YamlFrontmatter.extract(file_path=memory_dir / fname)
            if fm.get("title") == title:
                return fname.replace(".md", "")
        except Exception:
            continue
    return None


async def _lookup_file(memory_dir: Path, title: str) -> str | None:
    """统一查找文件：先按 title 匹配 frontmatter，再按文件名回退"""
    # 先按 frontmatter title 查找
    found = await _find_by_title(memory_dir, title)
    if found:
        return found
    # 回退：按文件名匹配
    f = _resolve_memory_file(memory_dir, title)
    if f:
        return f.stem
    return None


# ── 工具 1: 编辑正文 ──────────────────────────────────────────────


class MemoryEditContentTool(BaseTool):
    """编辑记忆正文内容：原文匹配 → 替换为新内容"""

    name = "memory_edit_content"
    description = (
        "编辑指定记忆文件的正文内容，通过字符串匹配完成替换。\n\n"
        "使用步骤：\n"
        "1. 先用 memory(operation=\\\"read\\\") 读取记忆全文，获取要替换的原文\n"
        "2. 调用此工具，传入 title + old_string + new_string\n\n"
        "注意事项：\n"
        "- old_string 必须在文件中唯一匹配，否则报错\n"
        "- new_string 可传空字符串来删除原文\n"
        "- 不会修改 frontmatter 元数据（tags/importance/title 等）"
    )

    def __init__(self) -> None:
        super().__init__()
        self._char_id: str = "ario"

    def set_char_id(self, char_id: str) -> None:
        """由 ws_server 在连接建立时设置当前角色 ID"""
        self._char_id = char_id

    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "要编辑的记忆标题（支持按标题或文件名匹配）",
            },
            "old_string": {
                "type": "string",
                "description": "要替换的原文（必须完整、唯一匹配，建议从 read 操作中复制）",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新内容。传空字符串可删除原文。默认空字符串",
            },
        },
        "required": ["title", "old_string"],
    }

    async def execute(  # type: ignore[override]
        self,
        title: str,
        old_string: str,
        new_string: str | None = "",
        **kwargs,
    ) -> dict:
        memory_dir = _get_memory_dir(char_id=self._char_id)

        # 剥离 .md 后缀
        if title.endswith(".md"):
            title = title[:-3]

        # 查找文件
        _lookup = await _lookup_file(memory_dir, title)
        if not _lookup:
            return {
                "status": "error",
                "error": f"未找到记忆 '{title}'，请先使用 memory(operation='list') 查看所有记忆",
            }

        file_path = memory_dir / f"{_lookup}.md"
        _new = new_string or ""

        # 文件写锁保护读-改-写操作
        lm = await LockManager.get_instance()
        async with lm.acquire(file_path):
            content = file_path.read_text("utf-8")
            fm, body = YamlFrontmatter.extract_io(content)

            if not old_string:
                return {
                    "status": "error",
                    "error": "edit_content 需要 old_string 参数",
                }

            occurrences = body.count(old_string)
            if occurrences == 0:
                return {
                    "status": "error",
                    "error": "未找到匹配的原文",
                }
            if occurrences > 1:
                return {
                    "status": "error",
                    "error": (
                        f"old_string 在文件中出现 {occurrences} 次，"
                        "请提供更精确的匹配文本以确保唯一性"
                    ),
                }

            new_body = body.replace(old_string, _new)

            # 重建文件（保留 frontmatter）
            if fm:
                fm["updated"] = YamlFrontmatter._now_str()
                fm["checksum"] = YamlFrontmatter._checksum(new_body)
                import yaml

                fm_str = yaml.dump(
                    fm,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                ).strip()
                new_content = f"---\n{fm_str}\n---\n\n{new_body}\n"
            else:
                new_content = new_body

            file_path.write_text(new_content, encoding="utf-8")

        # 更新索引
        index = MemoryIndex(memory_dir)
        await index.update_modify(_lookup, memory_dir)

        logger.info("[EDIT_CONTENT] 已编辑 %s.md (替换 '%s' → '%s')", _lookup, old_string, _new)
        return {"status": "ok", "result": f"已编辑 {_lookup}.md 正文内容"}


# ── 工具 2: 编辑标签 ──────────────────────────────────────────────


class MemoryEditTagsTool(BaseTool):
    """编辑记忆的标签列表（替换整个 tags 列表）"""

    name = "memory_edit_tags"
    description = (
        "替换指定记忆文件的标签列表。会完全覆盖现有的 tags，而非追加。\n\n"
        "使用：\n"
        "1. 先用 memory(operation='list') 查看所有记忆及其当前 tags\n"
        "2. 调用此工具传入新的 tags 列表\n\n"
        "示例：memory_edit_tags(title='出差记录', tags=['工作', '差旅', '报销'])"
    )

    def __init__(self) -> None:
        super().__init__()
        self._char_id: str = "ario"

    def set_char_id(self, char_id: str) -> None:
        """由 ws_server 在连接建立时设置当前角色 ID"""
        self._char_id = char_id

    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "要编辑的记忆标题（支持按标题或文件名匹配）",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "新的标签列表（会完全覆盖现有标签）。如 ['工作', '项目A']",
            },
        },
        "required": ["title", "tags"],
    }

    async def execute(  # type: ignore[override]
        self,
        title: str,
        tags: list[str],
        **kwargs,
    ) -> dict:
        if title.endswith(".md"):
            title = title[:-3]

        # 验证 tags
        if not isinstance(tags, list):
            return {"status": "error", "error": "tags 必须是字符串数组，如 ['工作', '项目A']"}

        memory_dir = _get_memory_dir(char_id=self._char_id)
        _lookup = await _lookup_file(memory_dir, title)
        if not _lookup:
            return {
                "status": "error",
                "error": f"未找到记忆 '{title}'，请先使用 memory(operation='list') 查看所有记忆",
            }

        file_path = memory_dir / f"{_lookup}.md"

        # 使用 YamlFrontmatter.update 更新 frontmatter（自动更新 updated + checksum）
        YamlFrontmatter.update(file_path, {"tags": sorted(tags)})

        # 更新索引
        index = MemoryIndex(memory_dir)
        await index.update_modify(_lookup, memory_dir)

        logger.info("[EDIT_TAGS] 已更新 %s.md tags=%s", _lookup, tags)
        return {
            "status": "ok",
            "result": f"已更新 {_lookup}.md 的标签: {sorted(tags)}",
        }


# ── 工具 3: 编辑标题 ──────────────────────────────────────────────


class MemoryEditTitleTool(BaseTool):
    """编辑记忆的显示标题（仅修改 frontmatter 中的 title 字段，不影响文件名）"""

    name = "memory_edit_title"
    description = (
        "修改记忆文件的显示标题（frontmatter 中的 title 字段）。\n"
        "注意：这只是修改展示用的标题，不影响文件的实际文件名。\n\n"
        "使用场景：\n"
        "- 记忆的标题写错了需要修正\n"
        "- 想让标题更准确地反映内容\n\n"
        "示例：memory_edit_title(title='出差记录', new_title='北京出差记录')"
    )

    def __init__(self) -> None:
        super().__init__()
        self._char_id: str = "ario"

    def set_char_id(self, char_id: str) -> None:
        """由 ws_server 在连接建立时设置当前角色 ID"""
        self._char_id = char_id

    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "当前的记忆标题（按当前标题或文件名匹配）",
            },
            "new_title": {
                "type": "string",
                "description": "新的显示标题（仅修改 frontmatter，不改变文件名）",
            },
        },
        "required": ["title", "new_title"],
    }

    async def execute(  # type: ignore[override]
        self,
        title: str,
        new_title: str,
        **kwargs,
    ) -> dict:
        if title.endswith(".md"):
            title = title[:-3]
        if not new_title.strip():
            return {"status": "error", "error": "new_title 不能为空"}

        memory_dir = _get_memory_dir(char_id=self._char_id)
        _lookup = await _lookup_file(memory_dir, title)
        if not _lookup:
            return {
                "status": "error",
                "error": f"未找到记忆 '{title}'，请先使用 memory(operation='list') 查看所有记忆",
            }

        file_path = memory_dir / f"{_lookup}.md"

        # 使用 YamlFrontmatter.update 更新 title
        YamlFrontmatter.update(file_path, {"title": new_title.strip()})

        # 更新索引（索引中的 summary 包含旧标题摘要，但不需要改）
        index = MemoryIndex(memory_dir)
        await index.update_modify(_lookup, memory_dir)

        # 如果新标题 sanitize 后与当前文件名不同，自动重命名文件 + 更新 LINKS.md
        sanitized_new = YamlFrontmatter.sanitize_filename(new_title.strip())
        rename_skipped = False
        if sanitized_new != _lookup:
            new_path = memory_dir / f"{sanitized_new}.md"
            if not new_path.exists():
                old_stem = _lookup
                async with (await LockManager.get_instance()).acquire(file_path):
                    file_path.rename(new_path)
                # 更新 LINKS.md 中所有引用
                lg = LinkGraph(memory_dir)
                await lg.rename_node(old_stem, sanitized_new)
                _lookup = sanitized_new
                logger.info(
                    "文件已随标题重命名: %s → %s", old_stem, sanitized_new
                )
            else:
                rename_skipped = True
                logger.warning(
                    "目标文件名已存在，跳过文件重命名: %s.md", sanitized_new
                )

        logger.info(
            "[EDIT_TITLE] 已更新 %s.md title='%s' → '%s'",
            _lookup, title, new_title,
        )

        result_msg = f"已将 {_lookup}.md 的显示标题更新为: {new_title.strip()}"
        if rename_skipped:
            result_msg += (
                f"（警告：目标文件名 {sanitized_new}.md 已存在，"
                f"文件名未随标题重命名）"
            )
        return {
            "status": "ok",
            "result": result_msg,
            "warn": (
                f"文件名 {sanitized_new}.md 已存在，跳过文件重命名。"
                "手动操作：合并文件或使用其它标题。"
            ) if rename_skipped else None,
        }


# ── 工具 4: 编辑重要度 ────────────────────────────────────────────


class MemoryEditImportanceTool(BaseTool):
    """编辑记忆的重要度（1-5，仅修改 frontmatter 中的 importance 字段）"""

    name = "memory_edit_importance"
    description = (
        "修改指定记忆文件的重要度等级（1-5）。\n\n"
        "重要度说明：\n"
        "1 = 临时 / 2 = 低价值 / 3 = 普通 / 4 = 重要 / 5 = 核心永久\n\n"
        "示例：memory_edit_importance(title='出差记录', new_importance=4)"
    )

    def __init__(self) -> None:
        super().__init__()
        self._char_id: str = "ario"

    def set_char_id(self, char_id: str) -> None:
        """由 ws_server 在连接建立时设置当前角色 ID"""
        self._char_id = char_id

    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "要编辑的记忆标题（支持按标题或文件名匹配）",
            },
            "new_importance": {
                "type": "integer",
                "description": "新的重要度 1-5：1=临时 / 2=低价值 / 3=普通 / 4=重要 / 5=核心永久",
                "minimum": 1,
                "maximum": 5,
            },
        },
        "required": ["title", "new_importance"],
    }

    async def execute(  # type: ignore[override]
        self,
        title: str,
        new_importance: int,
        **kwargs,
    ) -> dict:
        if title.endswith(".md"):
            title = title[:-3]

        # 验证
        if not isinstance(new_importance, int) or new_importance < 1 or new_importance > 5:
            return {"status": "error", "error": "new_importance 必须是 1-5 之间的整数"}

        memory_dir = _get_memory_dir(char_id=self._char_id)
        _lookup = await _lookup_file(memory_dir, title)
        if not _lookup:
            return {
                "status": "error",
                "error": f"未找到记忆 '{title}'，请先使用 memory(operation='list') 查看所有记忆",
            }

        file_path = memory_dir / f"{_lookup}.md"

        # 使用 YamlFrontmatter.update 更新 importance
        YamlFrontmatter.update(file_path, {"importance": new_importance})

        # 更新索引
        index = MemoryIndex(memory_dir)
        await index.update_modify(_lookup, memory_dir)

        logger.info("[EDIT_IMPORTANCE] 已更新 %s.md importance=%d", _lookup, new_importance)
        return {
            "status": "ok",
            "result": f"已将 {_lookup}.md 的重要度更新为: {new_importance}（{'⭐' * new_importance}）",
        }
