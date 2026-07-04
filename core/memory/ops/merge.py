"""MemoryTool mixin: 合并 + 剪枝"""

import logging
from datetime import datetime
from pathlib import Path

from core.memory.index import MemoryIndex
from core.memory.link_graph import LinkGraph
from core.memory.yaml_handler import YamlFrontmatter
from core.tools.file_lock import LockManager

logger = logging.getLogger(__name__)


class MemoryMergeMixin:
    """记忆合并与剪枝操作"""

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
        merged_importance: int = 3

        for sp in source_paths:
            content = sp.read_text("utf-8")
            fm, body = YamlFrontmatter.extract_io(content)
            merged_bodies.append(body.strip())

            # 取最早的 created
            created = fm.get("created", "")
            if created and (
                earliest_created is None or created < earliest_created
            ):
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

            # importance 取最大值
            imp = int(fm.get("importance", 3))
            if imp > merged_importance:
                merged_importance = imp

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

            fm_str = yaml.dump(
                tgt_fm,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            ).strip()
            target_path.write_text(
                f"---\n{fm_str}\n---\n\n{new_body}\n", encoding="utf-8"
            )
        else:
            # 目标不存在 → 创建新文件
            fm_metadata = {
                "type": merged_type,
                "source": "agent",
                "tags": sorted(merged_tags),
                "links": [],
                "importance": merged_importance,
            }
            full_content = YamlFrontmatter.inject(merged_body_text, fm_metadata)
            # 覆盖 created 为最早的
            full_fm, _ = YamlFrontmatter.extract_io(full_content)
            if earliest_created and full_fm:
                full_fm["created"] = earliest_created
                import yaml

                fm_str = yaml.dump(
                    full_fm,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                ).strip()
                full_content = f"---\n{fm_str}\n---\n\n{merged_body_text}\n"
            target_path.write_text(full_content, encoding="utf-8")

        # 源文件移入 _archive/
        archive_dir = memory_dir / "_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        lg = LinkGraph(memory_dir)
        archived_sources = []
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
        logger.info(
            "已合并 %d 个文件 → %s.md, 已归档: %s",
            len(sources),
            target,
            archived_sources,
        )
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
        lg = LinkGraph(memory_dir)
        await lg.initialize()

        orphans = await lg.find_orphans()
        archive_dir = memory_dir / "_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        pruned: list[str] = []
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
                fm, body = YamlFrontmatter.extract_io(content)
                if fm:
                    fm["status"] = "archived"
                    fm["archived_at"] = datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    import yaml

                    fm_str = yaml.dump(
                        fm,
                        default_flow_style=False,
                        allow_unicode=True,
                        sort_keys=False,
                    ).strip()
                    archived_content = f"---\n{fm_str}\n---\n\n{body}"
                    src_path.write_text(archived_content, encoding="utf-8")
            except Exception as e:
                logger.warning(
                    "prune 标记 frontmatter 失败 %s: %s", fname, e
                )

            async with lm.acquire(src_path):
                src_path.rename(dest)
            await index.remove_entry(stem)
            pruned.append(fname)
            logger.info("prune 已归档: %s", fname)

        self.invalidate_cache()
        return {"status": "ok", "result": {"pruned": pruned}}
