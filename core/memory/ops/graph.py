"""MemoryTool mixin: 图谱扩散 + 链接 + 审计"""

import logging
from collections import deque
from datetime import datetime
from pathlib import Path

from core.memory.index import MemoryIndex
from core.memory.link_graph import LinkGraph
from core.memory.yaml_handler import YamlFrontmatter

logger = logging.getLogger(__name__)


class MemoryGraphMixin:
    """图谱扩散检索、链接管理、审计"""

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
        lg = LinkGraph(memory_dir)
        await lg.initialize()
        graph = await lg.parse_links()
        forward = graph.get("forward", {})

        # 检查 seed 是否在图谱中（精确匹配）
        if seed in forward or any(
            seed in targets for targets in forward.values()
        ):
            results = await self._graph_search_memory(
                memory_dir,
                seed,
                query_tags,
                max_depth,
            )
            method = "graph_search"
        else:
            # 模糊匹配：在所有节点名中找包含 seed 的
            all_nodes: set[str] = set(forward.keys())
            for targets in forward.values():
                all_nodes.update(targets)
            fuzzy_match: str | None = None
            for node in sorted(all_nodes):
                if seed in node or node in seed:
                    fuzzy_match = node
                    break

            if fuzzy_match:
                results = await self._graph_search_memory(
                    memory_dir,
                    fuzzy_match,
                    query_tags,
                    max_depth,
                )
                method = "graph_search_fuzzy"
            else:
                # 标题→文件名映射兜底（兼容文件名非语义化的记忆）
                resolved = await self._find_by_title(memory_dir, seed)
                if resolved:
                    results = await self._graph_search_memory(
                        memory_dir,
                        resolved,
                        query_tags,
                        max_depth,
                    )
                    method = "graph_search"
                else:
                    # 降级：正文搜索（含 tags）
                    sr = await self._search_memory(memory_dir, index, seed)
                search_results = sr.get("result", {}).get("results", [])
                results = []
                for r in search_results:
                    results.append(
                        {
                            "file": r.get("file", ""),
                            "depth": 0,
                            "path": "(内容匹配)",
                            "relevance": 0.5,
                            "preview": r.get("preview", "")[:100],
                            "tags": [],
                            "type": "",
                        }
                    )
                method = "content_fallback"

        return {
            "status": "ok",
            "result": (
                {
                    "message": "没有找到匹配内容",
                    "count": 0,
                    "results": [],
                    "method": method,
                }
                if not results
                else {
                    "count": len(results),
                    "results": results,
                    "method": method,
                }
            ),
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
                        if updated and not self._is_within_days(
                            updated, 30, now
                        ):
                            relevance *= 0.8

                    # 取正文前 100 字作为预览
                    preview = (
                        body.strip()[:100] if body.strip() else "(空)"
                    )

                    results.append(
                        {
                            "file": f"{node}.md",
                            "depth": depth,
                            "path": " -> ".join(
                                f"[[{p}]]" for p in path
                            ),
                            "relevance": round(relevance, 2),
                            "preview": preview,
                            "tags": fm.get("tags", []) if fm else [],
                            "type": fm.get("type", "") if fm else "",
                        }
                    )

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
                    results.append(
                        {
                            "file": f.name,
                            "depth": 0,
                            "path": "(标签匹配)",
                            "relevance": 0.5 + 0.1 * len(matched),
                            "preview": body.strip()[:100]
                            if body.strip()
                            else "(空)",
                            "tags": fm.get("tags", []),
                            "type": fm.get("type", ""),
                        }
                    )
        results.sort(key=lambda r: r["relevance"], reverse=True)
        return results[:max_results]

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
            return {
                "status": "error",
                "error": f"目标文件不存在: {tgt}.md",
            }

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
        lg = LinkGraph(memory_dir)
        await lg.initialize()

        dead_links = await lg.detect_dead_links()
        orphans = await lg.find_orphans()

        total_files = len(
            [
                f
                for f in memory_dir.glob("*.md")
                if f.name not in ("MEMORY.md", "LINKS.md")
            ]
        )

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
