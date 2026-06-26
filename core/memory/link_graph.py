r"""LinkGraph — 双向链接图谱引擎 (LINKS.md 的读写/解析/维护)

管理记忆文件之间的 [[...]] 双向链接关系，为 graph_search(图谱扩散检索)提供数据基础。

集成点:
    - core.memory.yaml_handler.YamlFrontmatter._sanitize_wikilink: 链接安全过滤
    - core.tools.file_lock.LockManager: LINKS.md 并发写入保护
"""

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class LinkGraph:
    """双向链接图谱管理 — LINKS.md 的读写/解析/维护"""

    LINKS_FILENAME = "LINKS.md"

    # ── 表头定义 ──────────────────────────────────────────────
    NODE_HEADER = "| 文件名 | 标签 | 最后引用 | 引用次数 | 摘要 |"
    NODE_SEPARATOR = "|--------|------|---------|---------|------|"
    LINK_HEADER = "| 来源 | 目标 | 关系 | 建立时间 |"
    LINK_SEPARATOR = "|------|------|------|---------|"
    DEAD_HEADER = "| 来源 | 目标链接 | 状态 | 建议操作 |"
    DEAD_SEPARATOR = "|------|---------|------|---------|"

    def __init__(self, memory_dir: Path) -> None:
        """初始化 LinkGraph。

        Args:
            memory_dir: 记忆存储目录（包含 .md 记忆文件和 LINKS.md）
        """
        self._memory_dir = memory_dir
        self._links_path = memory_dir / self.LINKS_FILENAME

    # ── 公开方法 ──────────────────────────────────────────────

    async def initialize(self) -> None:
        """初始化 LINKS.md。

        如果 LINKS.md 不存在，创建带表头的空文件。
        如果已存在，不做任何操作。
        """
        if self._links_path.exists():
            logger.info("LINKS.md 已存在，跳过初始化: %s", self._links_path)
            return

        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire(self._links_path):
            content = self._build_empty_links()
            self._links_path.write_text(content, encoding="utf-8")
            logger.info("LINKS.md 已初始化: %s", self._links_path)

    async def parse_links(self) -> dict:
        """解析 LINKS.md → 正向图 + 反向图。

        Returns:
            ``{"forward": {source: {targets}}, "reverse": {target: {sources}}}``
            如果 LINKS.md 不存在，返回空图谱。
        """
        if not self._links_path.exists():
            return {"forward": {}, "reverse": {}}

        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire_read(self._links_path):
            content = self._links_path.read_text("utf-8")

        return self._parse_links_content(content)

    async def scan_wikilinks(self, file_path: Path) -> list[str]:
        r"""扫描 .md 文件中的 [[...]] 语法，返回所有链接目标列表。

        使用正则 re.findall(r'\[\[([^\]]+)\]\]', content) 提取，
        对每个提取的链接调用 _sanitize_wikilink() 安全检查。

        Args:
            file_path: 要扫描的 ``.md`` 文件路径

        Returns:
            安全过滤后的链接目标列表（已去重，保留首次出现顺序）

        Raises:
            FileNotFoundError: 如果文件不存在
            ValueError: 如果文件不是 ``.md`` 后缀
        """
        if file_path.suffix != ".md":
            raise ValueError(f"只支持 .md 文件: {file_path}")

        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire_read(file_path):
            content = file_path.read_text("utf-8")

        return self._extract_wikilinks(content)

    async def update_links(self, file_path: Path) -> None:
        """文件被 add/edit 后调用，重新扫描其 ``[[...]]`` 并更新 LINKS.md。

        流程:
            1. 解析当前 LINKS.md 获取图谱状态
            2. 扫描文件的旧链接和新链接，计算 diff
            3. 更新 LINKS.md 的链接表和节点表

        Args:
            file_path: 被 add/edit 的 ``.md`` 文件路径

        Raises:
            ValueError: 如果文件不是 ``.md`` 后缀
            FileNotFoundError: 如果文件不存在
        """
        if file_path.suffix != ".md":
            raise ValueError(f"只支持 .md 文件: {file_path}")

        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        source_stem = file_path.stem

        # 1. 解析当前 LINKS.md
        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()

        async with lm.acquire_write(self._links_path):
            # 在锁内重新读取（防止并发修改）
            current_content = (
                self._links_path.read_text("utf-8")
                if self._links_path.exists()
                else self._build_empty_links()
            )
            parsed = self._parse_links_content(current_content)
            forward = parsed["forward"]
            nodes = parsed.get("_nodes", {})
            dead_links_raw = parsed.get("_dead_links", [])

            # 2. 扫描文件当前的 [[...]] 链接
            from core.memory.yaml_handler import YamlFrontmatter
            fm, body = YamlFrontmatter.extract(file_path)
            new_targets = self._extract_wikilinks(body)

            # 如果 frontmatter 中有 links 字段，也合并进来
            fm_links = fm.get("links", [])
            if isinstance(fm_links, list):
                for link in fm_links:
                    if isinstance(link, str):
                        try:
                            sanitized = YamlFrontmatter._sanitize_wikilink(link)
                            if sanitized not in new_targets:
                                new_targets.append(sanitized)
                        except ValueError:
                            logger.warning("忽略无效的 frontmatter links 值: %s", link)

            old_targets = set(forward.get(source_stem, set()))

            new_targets_set = set(new_targets)
            added = new_targets_set - old_targets
            removed = old_targets - new_targets_set

            if not added and not removed:
                logger.debug("文件 %s 链接无变化，跳过更新", source_stem)
                return

            # 3. 更新图谱
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 更新 forward 图
            if new_targets_set:
                forward[source_stem] = new_targets_set
            else:
                forward.pop(source_stem, None)

            # 更新节点信息
            tags = fm.get("tags", [])
            if isinstance(tags, list):
                tag_str = ", ".join(str(t) for t in tags if t)
            else:
                tag_str = str(tags) if tags else ""

            summary = body.strip()[:50] if body.strip() else fm.get("type", "")
            nodes[source_stem] = {
                "tags": tag_str,
                "last_referenced": now_str[:10],
                "ref_count": str(len(new_targets_set)),
                "summary": summary,
            }

            # 为所有目标节点更新 last_referenced
            for tgt in new_targets_set:
                if tgt not in nodes:
                    nodes[tgt] = {
                        "tags": "",
                        "last_referenced": now_str[:10],
                        "ref_count": "0",
                        "summary": "",
                    }
                else:
                    nodes[tgt]["last_referenced"] = now_str[:10]

            # 4. 移除断链表中可能已恢复的条目
            alive_targets = new_targets_set | {
                t for src in forward for t in forward[src]
            }
            dead_links_raw = [
                dl for dl in dead_links_raw
                if dl["target"] not in alive_targets
                or dl["source"] != source_stem
            ]

            # 5. 重写 LINKS.md
            new_content = self._format_links_content(forward, nodes, dead_links_raw)
            self._links_path.write_text(new_content, encoding="utf-8")

            logger.info(
                "LINKS.md 已更新 [%s]: +%d 链接, -%d 链接",
                source_stem, len(added), len(removed),
            )

    async def detect_dead_links(self) -> list[dict]:
        """检测断链：LINKS.md 记录的链接目标文件已不存在。

        检查所有链接的目标文件是否仍在 ``memory/`` 目录中。

        Returns:
            ``[{"source": "Aerith", "target": "已删除的记忆", "status": "dead"}, ...]``
        """
        if not self._links_path.exists():
            return []

        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire_read(self._links_path):
            content = self._links_path.read_text("utf-8")

        parsed = self._parse_links_content(content)
        forward = parsed["forward"]

        dead_links: list[dict] = []
        for source, targets in forward.items():
            for target in targets:
                target_file = self._memory_dir / f"{target}.md"
                if not target_file.exists():
                    dead_links.append({
                        "source": source,
                        "target": target,
                        "status": "dead",
                    })

        return dead_links

    async def find_orphans(self) -> list[str]:
        """找到孤立文件（在 memory/ 目录中但不在任何节点的入链/出链中）。

        孤立文件是指存在于 ``memory/`` 目录中的 ``.md`` 文件，
        但其文件名（不含后缀）未出现在 LINKS.md 的节点表、出链或入链中。

        Returns:
            孤立文件名列表（不含 ``.md`` 后缀，不含 LINKS.md 自身）
        """
        # 收集所有 .md 文件
        all_md_files: set[str] = set()
        for f in self._memory_dir.glob("*.md"):
            if f.name == self.LINKS_FILENAME:
                continue
            all_md_files.add(f.stem)

        if not all_md_files:
            return []

        # 收集图谱中出现的所有文件名
        if self._links_path.exists():
            from core.tools.file_lock import LockManager
            lm = await LockManager.get_instance()
            async with lm.acquire_read(self._links_path):
                content = self._links_path.read_text("utf-8")
            parsed = self._parse_links_content(content)
            forward = parsed["forward"]
            reverse = parsed["reverse"]
            nodes = parsed.get("_nodes", {})
        else:
            forward = {}
            reverse = {}
            nodes = {}

        graph_names: set[str] = set(forward.keys())
        graph_names.update(reverse.keys())
        graph_names.update(nodes.keys())

        orphans = sorted(all_md_files - graph_names)
        return orphans

    async def remove_dead_links(self, link_list: list[dict]) -> None:
        """Agent 确认后，移除 LINKS.md 中的断链记录。

        从正向图谱中移除指定的断链，如果来源文件因此无出链则保留空节点。
        断链记录会被移动到 ``## 断链`` 表中供审计。

        Args:
            link_list: ``[{"source": "Aerith", "target": "已删除的记忆"}, ...]``
                      每项必须包含 ``source`` 和 ``target`` 键
        """
        if not link_list:
            return

        from core.tools.file_lock import LockManager
        lm = await LockManager.get_instance()
        async with lm.acquire_write(self._links_path):
            if not self._links_path.exists():
                logger.warning("LINKS.md 不存在，跳过断链移除")
                return

            content = self._links_path.read_text("utf-8")
            parsed = self._parse_links_content(content)
            forward = parsed["forward"]
            nodes = parsed.get("_nodes", {})
            dead_links_raw = parsed.get("_dead_links", [])

            now_str = datetime.now().strftime("%Y-%m-%d")

            for entry in link_list:
                source = entry.get("source", "")
                target = entry.get("target", "")
                if not source or not target:
                    continue

                # 从图谱中移除
                if source in forward and target in forward[source]:
                    forward[source].discard(target)
                    if not forward[source]:
                        del forward[source]

                # 添加到断链表
                dead_entry = {
                    "source": source,
                    "target": target,
                    "status": "已移除",
                    "action": f"于 {now_str} 移除",
                }
                # 避免重复记录
                dead_links_raw = [
                    dl for dl in dead_links_raw
                    if not (dl["source"] == source and dl["target"] == target)
                ]
                dead_links_raw.append(dead_entry)

                logger.info("断链已移除: %s → %s", source, target)

            new_content = self._format_links_content(forward, nodes, dead_links_raw)
            self._links_path.write_text(new_content, encoding="utf-8")
            logger.info("LINKS.md 断链清理完成")

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _extract_wikilinks(content: str) -> list[str]:
        r"""从内容中提取 [[...]] 链接并安全过滤。

        使用正则 re.findall(r'\[\[([^\]]+)\]\]', content) 提取所有 wiki 链接，
        然后通过 YamlFrontmatter._sanitize_wikilink() 进行安全检查。

        Args:
            content: 要扫描的文本内容

        Returns:
            安全过滤后的链接目标列表（去重，保持首次出现顺序）
        """
        from core.memory.yaml_handler import YamlFrontmatter

        raw_links = re.findall(r"\[\[([^\]]+)\]\]", content)
        sanitized: list[str] = []
        seen: set[str] = set()

        for link in raw_links:
            try:
                safe = YamlFrontmatter._sanitize_wikilink(link.strip())
                if safe and safe not in seen:
                    sanitized.append(safe)
                    seen.add(safe)
            except ValueError as e:
                logger.warning("过滤无效 wiki 链接 '%s': %s", link, e)

        return sanitized

    @staticmethod
    def _build_empty_links() -> str:
        """构建空的 LINKS.md 模板。"""
        lines = [
            "# 双向链接图谱",
            "",
            "## 节点",
            LinkGraph.NODE_HEADER,
            LinkGraph.NODE_SEPARATOR,
            "",
            "## 链接",
            LinkGraph.LINK_HEADER,
            LinkGraph.LINK_SEPARATOR,
            "",
            "## 断链",
            LinkGraph.DEAD_HEADER,
            LinkGraph.DEAD_SEPARATOR,
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _parse_links_content(content: str) -> dict:
        """解析 LINKS.md 内容 → 图谱结构。

        解析三个表格：
        - ``## 节点`` → _nodes
        - ``## 链接`` → forward/reverse
        - ``## 断链`` → _dead_links

        Args:
            content: LINKS.md 原始内容

        Returns:
            包含 ``forward``, ``reverse``, ``_nodes``, ``_dead_links`` 的字典
        """
        forward: dict[str, set[str]] = {}
        reverse: dict[str, set[str]] = {}
        nodes: dict[str, dict[str, str]] = {}
        dead_links: list[dict] = []

        current_section = ""
        in_table_header = False

        for line in content.splitlines():
            stripped = line.strip()

            # 检测章节
            if stripped.startswith("## "):
                current_section = stripped[3:].strip()
                in_table_header = False
                continue

            # 跳过空行
            if not stripped:
                in_table_header = False
                continue

            # 处理所有 | 开头的行（含分隔行和数据行）
            if stripped.startswith("|"):
                # 判断分隔行：去掉开头的 | 后，剩余部分是否只含 -、|、空格
                after_pipe = stripped[1:].strip()
                if after_pipe and all(c in "-| " for c in after_pipe):
                    in_table_header = False
                    continue

                # 按 | 拆分为列（保留空单元格）
                cells = stripped.split("|")
                # 去掉首尾空部分（| 前面的空串 和 | 后面的空串）
                if cells and cells[0] == "":
                    cells.pop(0)
                if cells and cells[-1] == "":
                    cells.pop()
                # 每列 strip 空白
                parts = [c.strip() for c in cells]

                # 跳过表头
                if parts and parts[0] == "文件名" and current_section == "节点":
                    in_table_header = True
                    continue
                if parts and parts[0] == "来源" and current_section in ("链接", "断链"):
                    in_table_header = True
                    continue

                if in_table_header:
                    in_table_header = False
                    continue

                # 解析数据行
                if current_section == "节点" and len(parts) >= 5:
                    fname = parts[0]
                    if fname:
                        nodes[fname] = {
                            "tags": parts[1] if len(parts) > 1 else "",
                            "last_referenced": parts[2] if len(parts) > 2 else "",
                            "ref_count": parts[3] if len(parts) > 3 else "0",
                            "summary": parts[4] if len(parts) > 4 else "",
                        }

                elif current_section == "链接" and len(parts) >= 4:
                    source_raw = parts[0].strip()
                    target_raw = parts[1].strip()

                    # 从 [[xxx]] 中提取纯文件名
                    source = re.sub(r"^\[\[|\]\]$", "", source_raw)
                    target = re.sub(r"^\[\[|\]\]$", "", target_raw)

                    if source and target:
                        forward.setdefault(source, set()).add(target)
                        reverse.setdefault(target, set()).add(source)

                elif current_section == "断链" and len(parts) >= 4:
                    source_raw = parts[0].strip()
                    target_raw = parts[1].strip()
                    status = parts[2].strip() if len(parts) > 2 else ""
                    action = parts[3].strip() if len(parts) > 3 else ""

                    source = re.sub(r"^\[\[|\]\]$", "", source_raw)
                    target = re.sub(r"^\[\[|\]\]$", "", target_raw)

                    if source and target:
                        dead_links.append({
                            "source": source,
                            "target": target,
                            "status": status,
                            "action": action,
                        })

        return {
            "forward": forward,
            "reverse": reverse,
            "_nodes": nodes,
            "_dead_links": dead_links,
        }

    @staticmethod
    def _format_links_content(
        forward: dict[str, set[str]],
        nodes: dict[str, dict[str, str]],
        dead_links: list[dict],
    ) -> str:
        """格式化链接数据为 LINKS.md 内容。

        Args:
            forward: 正向图 ``{source: {targets}}``
            nodes: 节点信息 ``{filename: {tags, last_referenced, ref_count, summary}}``
            dead_links: 断链列表 ``[{source, target, status, action}]``

        Returns:
            格式化的 LINKS.md 内容
        """
        lines = [
            "# 双向链接图谱",
            "",
            "## 节点",
            LinkGraph.NODE_HEADER,
            LinkGraph.NODE_SEPARATOR,
        ]

        # 按文件名排序，保证输出稳定
        sorted_nodes = sorted(nodes.items(), key=lambda x: x[0])
        for fname, info in sorted_nodes:
            tags = info.get("tags", "")
            last_ref = info.get("last_referenced", "")
            ref_count = info.get("ref_count", "0")
            summary = info.get("summary", "")
            lines.append(f"| {fname} | {tags} | {last_ref} | {ref_count} | {summary} |")

        lines.extend(["", "## 链接", LinkGraph.LINK_HEADER, LinkGraph.LINK_SEPARATOR])

        now_str = datetime.now().strftime("%Y-%m-%d")
        sorted_sources = sorted(forward.keys())
        for source in sorted_sources:
            targets = sorted(forward[source])
            for target in targets:
                # 尝试从节点表获取关系描述
                lines.append(f"| [[{source}]] | [[{target}]] | 关联 | {now_str} |")

        lines.extend(["", "## 断链", LinkGraph.DEAD_HEADER, LinkGraph.DEAD_SEPARATOR])

        for dl in dead_links:
            src = dl.get("source", "")
            tgt = dl.get("target", "")
            status = dl.get("status", "失效")
            action = dl.get("action", "建议移除或替换")
            lines.append(f"| [[{src}]] | [[{tgt}]] | {status} | {action} |")

        lines.append("")
        return "\n".join(lines)

    async def _get_file_metadata(self, file_path: Path) -> tuple[list[str], str, str]:
        """获取文件的标签、摘要、更新时间。

        Args:
            file_path: 记忆文件路径

        Returns:
            ``(tags_list, summary, last_updated_str)``
            如果文件无法解析，返回 ``([], "", "")``
        """
        from core.memory.yaml_handler import YamlFrontmatter

        if not file_path.exists():
            return [], "", ""

        try:
            from core.tools.file_lock import LockManager
            lm = await LockManager.get_instance()
            async with lm.acquire_read(file_path):
                content = file_path.read_text("utf-8")

            fm, body = YamlFrontmatter.extract_io(content)
            tags = fm.get("tags", [])
            if isinstance(tags, list):
                tags = [str(t) for t in tags]
            summary = body.strip()[:50] if body.strip() else ""
            last_updated = fm.get("updated", "")[:10]
            return tags, summary, last_updated
        except Exception as e:
            logger.warning("读取文件元数据失败 %s: %s", file_path, e)
            return [], "", ""
