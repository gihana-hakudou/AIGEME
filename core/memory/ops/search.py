"""MemoryTool mixin: 搜索 + 查重 + 标题查找"""

import logging
import os
from datetime import datetime
from pathlib import Path

from core.memory.index import MemoryIndex
from core.memory.yaml_handler import YamlFrontmatter

logger = logging.getLogger(__name__)


class MemorySearchMixin:
    """倒排索引搜索、内容查重、标题查找"""

    async def _search_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        query: str,
        tags_filter: list[str] | None = None,
    ) -> dict:
        """搜索记忆（跳过 >30 天未引用的文件，搜索不算引用）

        使用倒排索引将 O(n*m) 文件遍历 → O(1) 词查找 + O(k) 行读取
        tags_filter 可选，只返回包含指定标签的记忆
        """
        # 构建或更新倒排索引
        if (
            self._index_dirty
            or self._inverted_index is None
            or self._built_index_version != type(self)._INVERTED_INDEX_VERSION
        ):
            logger.info(
                "[INDEX] search: 重建倒排索引 (old=%d new=%d)",
                self._built_index_version,
                type(self)._INVERTED_INDEX_VERSION,
            )
            self._inverted_index = await self._build_inverted_index(memory_dir)
            self._built_index_version = type(self)._INVERTED_INDEX_VERSION
            self._index_dirty = False

        # 分词查询
        query_words = self._tokenize(query)
        if not query_words:
            return {
                "status": "ok",
                "result": {"message": "没有找到匹配内容", "count": 0, "results": []},
            }

        # debug: 确认倒排索引中是否有 tags 词
        for w in query_words:
            wh = self._inverted_index.get(w, {})
            if wh:
                tag_files = [f for f, lns in wh.items() if -1 in lns]
                if tag_files:
                    logger.info(
                        "[SEARCH_DEBUG] 词 '%s' 命中 tags 索引 → %s", w, tag_files
                    )

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
            return {
                "status": "ok",
                "result": {"message": "没有找到匹配内容", "count": 0, "results": []},
            }

        # 过滤 >30 天未引用的文件 + tags_filter 筛选 + 读取匹配行
        results = []
        now = datetime.now()
        index_data = await index.parse()

        for fname, matched_lines in hit_files.items():
            file_stem = fname.replace(".md", "")
            last_ref = index_data.get(file_stem, {}).get("last_referenced", "")
            if last_ref and not self._is_within_days(last_ref, 30, now):
                continue

            file_path = memory_dir / fname
            content = file_path.read_text("utf-8")

            # tags_filter 筛选
            if tags_filter:
                fm, _ = YamlFrontmatter.extract_io(content)
                file_tags = set(fm.get("tags", []))
                if not file_tags & set(tags_filter):
                    continue

            # 取正文行（倒排索引的行号基于 body，而非全文）
            fm, body = YamlFrontmatter.extract_io(content)
            body_lines = body.split("\n")
            matched = []
            has_tag_match = False
            for ln in sorted(matched_lines):
                if ln >= 0 and ln < len(body_lines):
                    matched.append(body_lines[ln].strip())
                elif ln == -1:
                    has_tag_match = True

            if matched or has_tag_match:
                # 读取 frontmatter 获取 title
                _title = file_stem
                try:
                    _fm, _ = YamlFrontmatter.extract_io(content)
                    if _fm.get("title"):
                        _title = _fm["title"]
                except Exception:
                    pass

                # 预览：优先用正文行，纯 tag 匹配则显示 tags 值
                tag_list = _fm.get("tags", []) or [] if _fm else []
                if not matched:
                    preview = f"[tags 匹配] {', '.join(str(t) for t in tag_list)}"
                else:
                    preview = matched[0][:80]

                results.append(
                    {
                        "id": file_stem,
                        "title": _title,
                        "file": fname,
                        "match_count": len(matched),
                        "preview": preview,
                    }
                )

        return {"status": "ok", "result": {"count": len(results), "results": results}}

    async def _check_similar_internal(
        self, memory_dir: Path, content: str
    ) -> dict | None:
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
        if (
            self._index_dirty
            or self._inverted_index is None
            or self._built_index_version != type(self)._INVERTED_INDEX_VERSION
        ):
            logger.info(
                "[INDEX] similar: 重建倒排索引 (old=%d new=%d)",
                self._built_index_version,
                type(self)._INVERTED_INDEX_VERSION,
            )
            self._inverted_index = await self._build_inverted_index(memory_dir)
            self._built_index_version = type(self)._INVERTED_INDEX_VERSION
            self._index_dirty = False

        hit_files: dict[str, set[int]] | None = None
        for word in words:
            word_hits = self._inverted_index.get(word, {})
            if not word_hits:
                continue
            if hit_files is None:
                hit_files = {f: set(lns) for f, lns in word_hits.items()}
            else:
                hit_files = {
                    f: hit_files[f] & word_hits[f]
                    for f in hit_files
                    if f in word_hits
                }

        if hit_files:
            best_file = max(hit_files, key=lambda f: len(hit_files[f]))
            overlap = len(hit_files[best_file])
            total_words = len(words)
            similarity = overlap / max(total_words, 1)
            if similarity >= 0.7:
                return {"file": best_file, "similarity": similarity}

        return None

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
                    results.append(
                        {
                            "file_a": f"{names[i]}.md",
                            "file_b": f"{names[j]}.md",
                            "similarity": round(similarity, 3),
                            "preview_a": a_preview,
                            "preview_b": b_preview,
                        }
                    )

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:10]  # 最多返回 Top-10

    async def _find_by_title(self, memory_dir: Path, title: str) -> str | None:
        """搜索所有记忆文件的 frontmatter，返回第一个 title 匹配的文件名（不含 .md）"""
        if not self._inverted_index or self._index_dirty:
            self._inverted_index = await self._build_inverted_index(memory_dir)
            self._index_dirty = False
        for fname in (
            list(self._inverted_index.get("__all__", {}))
            if "__all__" in self._inverted_index
            else os.listdir(memory_dir)
        ):
            if not fname.endswith(".md"):
                continue
            try:
                fm, _ = YamlFrontmatter.extract(file_path=memory_dir / fname)
                if fm.get("title") == title:
                    return fname.replace(".md", "")
            except Exception:
                continue
        return None
