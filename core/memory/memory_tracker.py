"""MemoryContextTracker — 记忆去重追踪器

职责：
  按需检索记忆、避免重复注入、Token 预算截断。

设计原则：
  1. Token 预算：单次注入不超过 MAX_INJECTION_TOKENS
  2. 混合检索：图谱扩散 + 倒排索引同时执行，合并去重

集成点（对应 loop.py 的 raact_stream）：
  — 在 build_variable_content() 之后、user_message 之前注入记忆上下文
  — 注入成功后调用 commit_injection() 记录已注入 ID
  — 所有异步方法都使用局部导入以避免循环依赖
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class MemoryContextTracker:
    """记忆注入追踪器 — 每轮按需检索记忆"""

    MAX_INJECTION_TOKENS = 2000     # 单次最大注入 Token

    def __init__(self) -> None:
        pass

    # ── 公开方法 ──────────────────────────────────────────────

    async def search_new_memory(
        self,
        user_message: str,
        round_num: int,
        memory_dir: Path,
        memory_tool: object | None,
    ) -> list[dict]:
        """搜索记忆，每轮都执行检索。

        触发条件：
          - 每轮都执行检索，不检查间隔

        Args:
            user_message: 当前轮次的用户消息原文
            round_num:    RaAct 当前轮次（从 1 开始计数）
            memory_dir:   记忆文件存储目录
            memory_tool:  MemoryTool 实例，提供 _tokenize/_search_memory
                          等底层能力。可以为 None（跳过检索）。

        Returns:
            截断后的结果列表，每项含 file/id/preview/relevance 等字段。
            空列表表示无需注入。
        """
        if memory_tool is None or not memory_dir.exists():
            return []

        # ── 执行混合检索 ────────────────────────────────────
        results = await self._retrieve_memories(user_message, memory_dir, memory_tool)

        # ── Token 截断 ──────────────────────────────────────
        truncated_results = self._truncate_by_tokens(results)

        logger.info(
            "[MEMTRACK] round=%d 检索结果: 原始 %d 条 → 截断 %d 条",
            round_num, len(results), len(truncated_results),
        )
        return truncated_results

    @staticmethod
    def get_context_text(results: list[dict]) -> str:
        """组装去重后的记忆上下文文本，供 User Prompt 注入

        格式：
          ## 相关记忆（按相关性）
          - [[A]] -> [[B]] (相关性: 0.9): preview text
          - [[C]] (相关性: 0.5): another text

        Args:
            results: 去重截断后的记忆结果列表

        Returns:
            注入文本（空列表时返回空字符串）
        """
        if not results:
            return ""

        lines = ["## 相关记忆（按相关性）"]
        for r in results:
            path = r.get("path", "")
            preview = r.get("preview", "")
            rel = r.get("relevance", 0)
            if path:
                lines.append(f"- {path} (相关性: {rel}): {preview}")
            else:
                fname = r.get("file", "")
                # 去掉 .md 后缀，转为 wiki 链接格式
                display = fname.replace(".md", "") if fname else ""
                lines.append(f"- [[{display}]] (相关性: {rel}): {preview}")

        context = "\n".join(lines)

        # Token 预估和截断（中文约 1.5 chars/token，保守按 2）
        token_estimate = len(context) // 2
        if token_estimate > MemoryContextTracker.MAX_INJECTION_TOKENS:
            budget_chars = MemoryContextTracker.MAX_INJECTION_TOKENS * 2
            context = context[:budget_chars] + "\n...（已截断）"

        return context

    # ── 内部方法 ─────────────────────────────────────────────

    async def _retrieve_memories(
        self,
        query: str,
        memory_dir: Path,
        memory_tool: object,
    ) -> list[dict]:
        """混合检索 — 图谱扩散 + 倒排索引同时执行，合并去重

        检索策略：
          1. 提取用户消息中的核心实体（分词取前 3 个长词）
          2. 用每个实体作 seed 做 graph_search（max_depth=2）
          3. 同时用倒排索引搜索全文内容
          4. 合并结果、去重、按相关性排序

        Args:
            query:      用户消息原文
            memory_dir: 记忆目录
            memory_tool: MemoryTool 实例（提供 _tokenize/_search_memory）

        Returns:
            去重合并后的结果列表（每项含 file/id/preview/relevance）
        """
        from core.memory.yaml_handler import YamlFrontmatter
        from core.memory.index import MemoryIndex

        words = memory_tool._tokenize(query)
        seeds = [w for w in words if len(w) >= 2][:3]

        results: list[dict] = []
        seen_files: set[str] = set()

        # ── 方式 1：graph_search（图谱扩散）──────────────────
        for seed in seeds:
            try:
                gr = await memory_tool._graph_search_memory(
                    memory_dir, seed,
                    query_tags=None, max_depth=2, max_results=5,
                )
                for r in gr:
                    fname = r.get("file", "")
                    if fname not in seen_files:
                        seen_files.add(fname)
                        file_path = memory_dir / fname
                        if file_path.exists():
                            content = file_path.read_text("utf-8")
                            fm, _ = YamlFrontmatter.extract_io(content)
                            r["id"] = fm.get("id", "") if fm else ""
                            # 重要度加成：importance 5 → +0.4, 1 → +0.0
                            imp = int(fm.get("importance", 3)) if fm else 3
                            r["relevance"] = r.get("relevance", 0) + (imp - 1) * 0.1
                        results.append(r)
            except AttributeError:
                logger.debug("[MEMTRACK] _graph_search_memory 不可用，跳过图谱检索")
                break
            except Exception:
                logger.debug("[MEMTRACK] graph_search(%s) 失败, 跳过", seed, exc_info=True)
                continue

        # ── 方式 2：倒排索引（全文搜索）──────────────────────
        try:
            index = MemoryIndex(memory_dir)
            sr = await memory_tool._search_memory(memory_dir, index, query)
            search_results = sr.get("result", {}).get("results", [])
            for r in search_results:
                fname = r.get("file", "")
                if fname not in seen_files:
                    seen_files.add(fname)
                    file_path = memory_dir / fname
                    preview = ""
                    r_id = ""
                    imp = 3
                    if file_path.exists():
                        content = file_path.read_text("utf-8")
                        fm, body = YamlFrontmatter.extract_io(content)
                        r_id = fm.get("id", "") if fm else ""
                        preview = body.strip()[:100] if body.strip() else "(空)"
                        imp = int(fm.get("importance", 3)) if fm else 3
                    results.append({
                        "file": fname,
                        "id": r_id,
                        "path": "",
                        "relevance": 0.3 + (imp - 1) * 0.1,
                        "preview": preview or r.get("preview", ""),
                    })
        except Exception:
            logger.debug("[MEMTRACK] search 执行失败", exc_info=True)

        # 按相关性降序排序
        results.sort(key=lambda r: r.get("relevance", 0), reverse=True)
        return results[:10]

    def _has_new_entities(self, words: set[str], memory_dir: Path) -> bool:
        """检测用户消息中是否有未在前 N 轮讨论过的实体

        检查分词中有没有与现有记忆文件名不相交的词。
        如果有文件名中不存在的词，说明可能是新话题。

        Args:
            words:      从用户消息中提取的分词集合
            memory_dir: 记忆目录

        Returns:
            True 如果至少有 2 个不在现有文件名中的词（判定为新话题）
        """
        existing_files = {
            f.stem for f in memory_dir.glob("*.md")
            if f.name not in ("MEMORY.md", "LINKS.md")
        }
        # 将文件名集合转为字符串用于模糊匹配
        existing_str = str(existing_files).lower()
        new_words = [
            w for w in words
            if w not in existing_str and len(w) >= 2
        ]
        return len(new_words) >= 2

    @staticmethod
    def _truncate_by_tokens(results: list[dict]) -> list[dict]:
        """按 Token 预算截断结果列表

        按顺序保留结果，每条结果消耗其预览文本的预估 Token。
        超出预算时截断最后一条结果的预览文本。

        Args:
            results: 去重后的结果列表

        Returns:
            Token 截断后的结果列表
        """
        token_budget = MemoryContextTracker.MAX_INJECTION_TOKENS
        truncated: list[dict] = []
        for r in results:
            preview = r.get("preview", "")
            estimated = len(preview) // 2 + 10  # 每条加 10 Token 开销
            if estimated <= token_budget:
                truncated.append(r)
                token_budget -= estimated
            else:
                # 截断预览文本以适配剩余预算
                if token_budget > 10:
                    r = dict(r)  # 不修改原始数据
                    r["preview"] = preview[:(token_budget - 10) * 2]
                    truncated.append(r)
                break
        return truncated
