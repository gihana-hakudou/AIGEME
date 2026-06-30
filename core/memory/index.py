"""MEMORY.md 索引管理 — 解析/更新/过期"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from core.tools.file_lock import LockManager


class MemoryIndex:
    """MEMORY.md 索引管理"""

    INDEX_FILENAME = "MEMORY.md"

    def __init__(self, memory_dir: Path) -> None:
        self._memory_dir = memory_dir
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = memory_dir / self.INDEX_FILENAME

    async def parse(self) -> dict[str, dict[str, Any]]:
        """解析 MEMORY.md → {文件名: {条目数, 最后更新, 最后引用, 标签, 摘要}}"""
        if not self._index_path.exists():
            return {}

        lm = await LockManager.get_instance()
        async with lm.acquire_read(self._index_path):
            content = self._index_path.read_text("utf-8")
            return self._parse_index_content(content)

    def _parse_index_content(self, content: str) -> dict[str, dict[str, Any]]:
        """解析 MEMORY.md 内容

        兼容新旧两种格式：
        - 旧格式：5 列（文件 | 条目数 | 最后更新 | 最后引用 | 摘要）
        - 新格式：6 列（文件 | 条目数 | 最后更新 | 最后引用 | 标签 | 摘要）
        """
        index: dict[str, dict[str, Any]] = {}
        current_section = ""
        # 通过分隔线（|---|）的列数检测格式
        has_tag_column: bool | None = None

        for line in content.splitlines():
            line = line.strip()
            if line.startswith("## "):
                current_section = line[3:].strip()
            elif line.startswith("|---"):
                # 检测分隔列数：5 列旧格式 vs 6 列新格式
                parts = [p for p in line.split("|") if p.strip()]
                has_tag_column = len(parts) >= 6
            elif line.startswith("| ") and "|" in line[2:]:
                parts = [p.strip() for p in line.split("|")]
                # parts[0] = ""(leading), parts[1]=文件, ..., parts[-1]=""(trailing)
                if len(parts) < 3:
                    continue
                file_name = parts[1]
                if not file_name or file_name in ("文件", "(暂无)", "—"):
                    continue

                if has_tag_column and len(parts) >= 8:
                    # 新格式：标签列在 parts[5]
                    index[file_name] = {
                        "section": current_section,
                        "entries": parts[2] if len(parts) > 2 else "0",
                        "last_updated": parts[3] if len(parts) > 3 else "",
                        "last_referenced": parts[4] if len(parts) > 4 else "",
                        "tags": parts[5] if len(parts) > 5 else "",
                        "summary": parts[6] if len(parts) > 6 else "",
                    }
                elif len(parts) >= 7:
                    # 旧格式：无标签列，parts[5] 是摘要
                    index[file_name] = {
                        "section": current_section,
                        "entries": parts[2] if len(parts) > 2 else "0",
                        "last_updated": parts[3] if len(parts) > 3 else "",
                        "last_referenced": parts[4] if len(parts) > 4 else "",
                        "tags": "",
                        "summary": parts[5] if len(parts) > 5 else "",
                    }
        return index

    async def update_after_add(
        self, title: str, metadata: dict[str, Any]
    ) -> None:
        """新增记忆后更新索引"""
        existing = await self.parse()
        existing[title] = metadata

        await self._write_index(existing)

    async def update_reference(self, title: str) -> None:
        """读取记忆后更新引用时间"""
        existing = await self.parse()
        if title in existing:
            existing[title]["last_referenced"] = datetime.now().strftime("%Y-%m-%d")
            await self._write_index(existing)

    async def update_modify(self, title: str, memory_dir: Path | None = None) -> None:
        """编辑记忆后更新最后修改时间和条目数"""
        existing = await self.parse()
        if title in existing:
            existing[title]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            # 重新统计条目数
            if memory_dir:
                file_path = memory_dir / f"{title}.md"
                if file_path.exists():
                    count = sum(1 for line in file_path.read_text("utf-8").splitlines() if line.startswith("- ["))
                    existing[title]["entries"] = str(count)
            await self._write_index(existing)

    async def remove_entry(self, title: str) -> None:
        """删除记忆后移除索引条目"""
        existing = await self.parse()
        existing.pop(title, None)
        await self._write_index(existing)

    async def write_initial_index(self, sections: list[dict[str, str]]) -> None:
        """写入初始 MEMORY.md"""
        lines = [
            "# 记忆索引",
            "",
            f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        for section in sections:
            lines.append(f"## {section['name']}")
            lines.append("| 文件 | 条目数 | 最后更新 | 最后引用 | 标签 | 摘要 |")
            lines.append("|------|--------|---------|---------|------|------|")
            if section.get("hint"):
                lines.append(f"| (暂无) | 0 | - | - | - | {section['hint']} |")
            lines.append("")

        self._index_path.write_text("\n".join(lines), encoding="utf-8")

    async def write_initial_with_template(self, template_content: str, sections: list[dict[str, str]]) -> None:
        """用模板内容初始化 MEMORY.md，在模板下方追加分区表格"""
        lines = [
            template_content.strip(),
            "",
            "---",
            "",
        ]
        for section in sections:
            lines.append(f"## {section['name']}")
            lines.append("| 文件 | 条目数 | 最后更新 | 最后引用 | 标签 | 摘要 |")
            lines.append("|------|--------|---------|---------|------|------|")
            if section.get("hint"):
                lines.append(f"| (暂无) | 0 | - | - | - | {section['hint']} |")
            lines.append("")

        self._index_path.write_text("\n".join(lines), encoding="utf-8")

    async def _write_index(self, entries: dict[str, dict[str, Any]]) -> None:
        """将索引写回 MEMORY.md（保留 `---` 分隔线以上的模板头部，加文件锁）"""
        lm = await LockManager.get_instance()
        async with lm.acquire(self._index_path):
            # 按 section 分组
            sections: dict[str, list[tuple[str, dict]]] = {}
            for name, meta in entries.items():
                sec = meta.get("section", "其他")
                sections.setdefault(sec, []).append((name, meta))

            # 保留现有文件中的模板头部（`---` 之前的内容）
            header = None
            if self._index_path.exists():
                current = self._index_path.read_text("utf-8")
                lines = current.splitlines()
                sep_idx = -1
                for i, line in enumerate(lines):
                    if line.strip() == "---":
                        sep_idx = i
                        break
                if sep_idx >= 0:
                    header = "\n".join(lines[:sep_idx + 1])

            # 构造新的索引体
            body_lines = []
            for section, items in sections.items():
                body_lines.append(f"## {section}")
                body_lines.append("| 文件 | 条目数 | 最后更新 | 最后引用 | 标签 | 摘要 |")
                body_lines.append("|------|--------|---------|---------|------|------|")
                for name, meta in items:
                    tags_str = meta.get("tags", "")
                    if isinstance(tags_str, list):
                        tags_str = ", ".join(tags_str)
                    body_lines.append(
                        f"| {name} | {meta.get('entries', '?')} "
                        f"| {meta.get('last_updated', '-')} "
                        f"| {meta.get('last_referenced', '-')} "
                        f"| {tags_str} "
                        f"| {meta.get('summary', '')} |"
                    )
                body_lines.append("")

            if header:
                final = header + "\n\n" + "\n".join(body_lines)
            else:
                final = "\n".join([
                    "# 记忆索引",
                    "",
                    f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    "",
                ] + body_lines)

            self._index_path.write_text(final, encoding="utf-8")

    async def get_entries_summary(self) -> str:
        """获取索引的简述文本（注入 prompt 用）"""
        if not self._index_path.exists():
            return ""
        return self._index_path.read_text("utf-8")
