"""MemoryTool mixin: CRUD 操作 (add/read/del/edit/list/append)"""

import logging
from datetime import datetime
from pathlib import Path

from core.memory.index import MemoryIndex
from core.memory.yaml_handler import YamlFrontmatter
from core.tools.file_lock import LockManager

logger = logging.getLogger(__name__)


class MemoryCrudMixin:
    """记忆 CRUD 操作：新增、读取、删除、编辑、列表、追加"""

    async def _add_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        title: str,
        content: str,
        type: str,
        importance: int = 3,
        tags: list[str] | None = None,
        display_title: str | None = None,
    ) -> dict:
        """新增记忆文件

        title: 文件名（自动生成的时间戳），display_title: 显示标题（存入 frontmatter）
        """
        _tags = tags or []
        file_path = memory_dir / f"{title}.md"
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M")

        entry = f"- [{timestamp}] [agent] [{type}] {content}\n"

        # 文件写锁保护追加操作
        lm = await LockManager.get_instance()
        async with lm.acquire(file_path):
            if not file_path.exists():
                # ★ B1: 新文件 → 注入 YAML frontmatter
                fm_metadata = {
                    "type": type,
                    "source": "agent",
                    "tags": _tags,
                    "importance": importance,
                }
                if display_title:
                    fm_metadata["title"] = display_title
                full_content = YamlFrontmatter.inject(entry, fm_metadata)
                file_path.write_text(full_content, encoding="utf-8")
            else:
                # 已有文件 → 只追加条目（不改变 frontmatter）
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(entry)
                # 更新 frontmatter 中的 checksum 和 updated
                YamlFrontmatter.update(file_path, {})

        # 更新索引
        await index.update_after_add(
            title,
            {
                "entries": self._count_entries(file_path),
                "last_updated": now.strftime("%Y-%m-%d"),
                "last_referenced": now.strftime("%Y-%m-%d"),
                "tags": _tags,
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
                logger.warning(
                    "[MEMORY_DEBUG] add → MEMORY.md 未包含 '%s' ✗\n%s",
                    title,
                    md_content[:500],
                )
        else:
            logger.warning(
                "[MEMORY_DEBUG] MEMORY.md 不存在！memory_dir=%s", memory_dir
            )

        result_parts = {"id": title, "file": f"{title}.md", "added": entry.strip()}
        if display_title:
            result_parts["title"] = display_title
        return {"status": "ok", "result": result_parts}

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
        lm = await LockManager.get_instance()
        async with lm.acquire_read(file_path):
            content = file_path.read_text("utf-8")

        # ★ B2: 剥离 YAML frontmatter，metadata 单独返回
        fm, body = YamlFrontmatter.extract_io(content)

        # 更新引用时间（MemoryIndex 内部的 RWLock 保护 MEMORY.md）
        await index.update_reference(title)

        result = {"id": title, "file": f"{title}.md", "content": body.strip()}
        if fm:
            result["metadata"] = {
                "type": fm.get("type"),
                "importance": fm.get("importance", 3),
                "created": fm.get("created"),
                "updated": fm.get("updated"),
                "title": fm.get("title"),
                "tags": fm.get("tags", []),
                "links": fm.get("links", []),
                "source": fm.get("source"),
                "status": fm.get("status"),
            }
            if fm.get("title"):
                result["title"] = fm["title"]
        return {"status": "ok", "result": result}

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
            return {
                "status": "error",
                "error": f"{title}.md 已在 _archive/ 中，请勿重复归档",
            }

        # 在文件 frontmatter 中标记已归档
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = archive_dir / f"{title}_{timestamp}.md"

        try:
            content = file_path.read_text("utf-8")
            fm, body = YamlFrontmatter.extract_io(content)
            if fm:
                fm["status"] = "archived"
                fm["archived_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                import yaml

                fm_str = yaml.dump(
                    fm,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                ).strip()
                archived_content = f"---\n{fm_str}\n---\n\n{body}"
                file_path.write_text(archived_content, encoding="utf-8")
        except Exception as e:
            logger.warning("归档标记 frontmatter 失败: %s", e)

        # 文件写锁保护移动操作
        lm = await LockManager.get_instance()
        async with lm.acquire(file_path):
            file_path.rename(dest)

        # 从索引中移除条目（归档文件的 frontmatter 已有 status: archived）
        await index.remove_entry(title)
        self.invalidate_cache()
        logger.info("记忆已归档: %s → _archive/%s_%s.md", title, title, timestamp)
        return {
            "status": "ok",
            "result": f"已归档 {title}.md → _archive/{title}_{timestamp}.md",
        }

    async def _edit_memory(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        title: str,
        old_string: str,
        new_string: str | None,
        tags: list[str] | None = None,
        new_importance: int | None = None,
    ) -> dict:
        """编辑记忆

        支持通过 tags 更新标签，new_importance 更新重要性
        """
        file_path = memory_dir / f"{title}.md"
        if not file_path.exists():
            return {"status": "error", "error": f"记忆文件不存在: {title}.md"}

        # 文件写锁保护读-改-写操作
        lm = await LockManager.get_instance()
        async with lm.acquire(file_path):
            content = file_path.read_text("utf-8")

            fm, body = YamlFrontmatter.extract_io(content)

            # ── 只更新元数据（无需 body 修改）──
            if not old_string and new_string is None:
                updates = {}
                if tags is not None:
                    updates["tags"] = sorted(tags)
                if new_importance is not None:
                    updates["importance"] = new_importance
                if updates:
                    fm.update(updates)
                    fm["updated"] = YamlFrontmatter._now_str()
                    fm["checksum"] = YamlFrontmatter._checksum(body)
                    import yaml

                    fm_str = yaml.dump(
                        fm,
                        default_flow_style=False,
                        allow_unicode=True,
                        sort_keys=False,
                    ).strip()
                    new_content = f"---\n{fm_str}\n---\n\n{body}\n"
                    file_path.write_text(new_content, encoding="utf-8")
                    await index.update_modify(title, memory_dir)
                    self.invalidate_cache()
                    return {
                        "status": "ok",
                        "result": f"已更新 {title}.md 元数据",
                    }
                return {"status": "ok", "result": "没有需要更新的元数据"}

            # ── 修改正文 ──
            if not old_string:
                return {
                    "status": "error",
                    "error": "edit 操作需要 old_string 参数",
                }

            if new_string is None:
                new_string = ""

            occurences = body.count(old_string)
            if occurences == 0:
                return {
                    "status": "error",
                    "error": "未找到匹配的原文",
                }
            if occurences > 1:
                return {
                    "status": "error",
                    "error": f"old_string 在文件中出现 {occurences} 次，请提供更精确的匹配文本以确保唯一性",
                }

            new_body = body.replace(old_string, new_string if new_string else "")

            # 重建文件内容（保留 frontmatter）
            if fm:
                updates = {}
                if tags is not None:
                    updates["tags"] = sorted(tags)
                if new_importance is not None:
                    updates["importance"] = new_importance
                updates["updated"] = YamlFrontmatter._now_str()
                updates["checksum"] = YamlFrontmatter._checksum(new_body)
                fm.update(updates)
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

        await index.update_modify(title, memory_dir)
        self.invalidate_cache()
        return {"status": "ok", "result": f"已编辑 {title}.md"}

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
        entry_count = sum(
            1
            for line in content.splitlines()
            if line.startswith("| ")
            and "|" in line[2:]
            and not line.strip().startswith("|---")
        )
        logger.info(
            "[MEMORY_DEBUG] list → MEMORY.md 大小=%d 字节, 条目行数=%d",
            len(content),
            entry_count,
        )

        entries: list[dict] = []
        if not include_all:
            # 过滤过期文件 + 已归档条目
            index_data = await index.parse()
            now = datetime.now()
            filtered_lines: list[str] = []
            for line in content.splitlines():
                if line.startswith("| ") and "|" in line[2:]:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 5 and parts[1]:
                        _id = parts[1]
                        # 过滤过期文件
                        last_ref = index_data.get(_id, {}).get(
                            "last_referenced", ""
                        )
                        if last_ref and not self._is_within_days(
                            last_ref, 30, now
                        ):
                            continue
                        # 过滤已归档条目
                        if index_data.get(_id, {}).get("status") == "archived":
                            continue
                        # 收集结构化条目
                        entries.append(
                            {
                                "id": _id,
                                "type": index_data.get(_id, {}).get("section", ""),
                                "tags": index_data.get(_id, {}).get("tags", ""),
                                "summary": index_data.get(_id, {}).get(
                                    "summary", ""
                                ),
                                "updated": index_data.get(_id, {}).get(
                                    "last_updated", ""
                                ),
                            }
                        )
                filtered_lines.append(line)
            content = "\n".join(filtered_lines)

        return {"status": "ok", "result": {"index": content, "entries": entries}}

    async def _append_to_existing(
        self,
        memory_dir: Path,
        index: MemoryIndex,
        filename: str,
        new_string: str,
        tags: list[str] | None = None,
    ) -> dict:
        """向已存在的记忆文件追加条目（查重命中后使用）"""
        file_path = memory_dir / filename
        now = datetime.now()

        lm = await LockManager.get_instance()
        async with lm.acquire(file_path):
            # 合并 tags 到 frontmatter
            _tags = tags or []
            if _tags:
                fm, _ = YamlFrontmatter.extract(file_path)
                existing_tags = set(fm.get("tags", []))
                new_unique = [t for t in _tags if t not in existing_tags]
                if new_unique:
                    YamlFrontmatter.update(
                        file_path, {"tags": sorted(existing_tags | set(_tags))}
                    )
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(new_string)
            # 更新 frontmatter 的 checksum 和 updated
            YamlFrontmatter.update(file_path, {})

        title = filename.replace(".md", "")
        await index.update_after_add(
            title,
            {
                "entries": self._count_entries(file_path),
                "last_updated": now.strftime("%Y-%m-%d"),
                "last_referenced": now.strftime("%Y-%m-%d"),
                "tags": tags or [],
                "summary": new_string.strip()[:30],
                "section": "其他",
            },
        )

        self.invalidate_cache()

        fp = memory_dir / f"{title}.md"
        cur_imp = 3
        if fp.exists():
            fm2, _ = YamlFrontmatter.extract(fp)
            cur_imp = fm2.get("importance", 3)
        return {
            "status": "ok",
            "result": {"file": filename, "added": new_string.strip()},
            "note": f"记忆重要度未变更，当前记忆重要度：{cur_imp}",
        }
