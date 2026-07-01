"""修复老记忆文件格式

变更：
1. [type        ] → [type]（去除类型字段尾部空格填充）
2. 从 ★★★★☆ 提取 importance（★个数）写入 frontmatter
3. 从文件名提取 title 写入 frontmatter（老记忆文件名=标题）
4. 备份原始文件到 bak/ 目录

用法：
  python scripts/fix_old_memories.py [--dry-run]
"""

import argparse
import logging
import re
import shutil
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / ".AIGEME" / ".data" / "local"

# 排除的文件/目录
EXCLUDE_DIRS = {"reminders"}
EXCLUDE_FILES = {"MEMORY.md", "LINKS.md"}

# 匹配正文条目中的 [type        ] 模式
ENTRY_TYPE_PAD_RE = re.compile(r"\[(\w+)\s+\]")


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """简易 frontmatter 解析（不依赖 yaml 库，兼容破损格式）"""
    if not content.startswith("---\n"):
        return {}, content

    parts = content.split("\n---\n", 1)
    if len(parts) < 2:
        return {}, content

    fm_text = parts[0][4:]  # 去掉开头的 "---\n"
    body = parts[1]

    fm: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip("'\"")
    return fm, body


def build_frontmatter(fm: dict) -> str:
    """将 dict 序列化为 YAML frontmatter 字符串"""
    lines = ["---"]
    for key, val in fm.items():
        if val is None:
            continue
        if isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        elif isinstance(val, int):
            lines.append(f"{key}: {val}")
        elif isinstance(val, list):
            items = ", ".join(str(v) for v in val)
            lines.append(f"{key}: [{items}]")
        else:
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def fix_file(filepath: Path, dry_run: bool = False) -> dict:
    """修复单个记忆文件，返回操作记录"""
    result = {"file": str(filepath), "changes": []}

    content = filepath.read_text("utf-8")
    fm, body = parse_frontmatter(content)
    original_body = body
    original_fm = dict(fm)

    changes = []

    # ── Fix 1: 补充 title（老记忆没有 title 字段，用文件名）──
    stem = filepath.stem  # 文件名不含 .md
    if "title" not in fm or not fm["title"]:
        fm["title"] = stem
        changes.append(f"添加 title: {stem}")

    # ── Fix 2: 从 ★★☆ 提取 importance ──
    importance_entries = re.findall(r"[★]+[☆]*", body)
    if importance_entries:
        # 取第一个条目的 ★ 数作为文件的默认 importance
        star_count = importance_entries[0].count("★")
        if "importance" not in fm:
            fm["importance"] = star_count
            changes.append(f"添加 importance: {star_count}（来自 ★ 数）")
    else:
        if "importance" not in fm:
            fm["importance"] = 3
            changes.append("添加 importance: 3（默认值，未找到 ★ 标记）")

    # ── Fix 3: [type        ] → [type] ──
    new_body, replace_count = ENTRY_TYPE_PAD_RE.subn(r"[\1]", body)
    if replace_count > 0:
        body = new_body
        changes.append(f"修复 {replace_count} 处类型字段空格填充")

    # ── 无变化则跳过 ──
    if not changes:
        return result

    result["changes"] = changes

    if dry_run:
        return result

    # ── 备份 ──
    bak_dir = filepath.parent / "bak"
    bak_dir.mkdir(parents=True, exist_ok=True)
    bak_path = bak_dir / f"{filepath.name}.bak"
    shutil.copy2(filepath, bak_path)
    result["backup"] = str(bak_path)

    # ── 重写文件 ──
    new_frontmatter = build_frontmatter(fm)
    new_content = new_frontmatter + body.strip() + "\n"
    filepath.write_text(new_content, encoding="utf-8")
    result["rewritten"] = True

    return result


def scan_memory_dirs() -> list[Path]:
    """扫描所有需要修复的记忆文件"""
    files = []
    if not DATA_DIR.exists():
        logger.warning("[SKIP] 数据目录不存在: %s", DATA_DIR)
        return files

    for char_dir in DATA_DIR.iterdir():
        if not char_dir.is_dir():
            continue
        memory_dir = char_dir / "memory"
        if not memory_dir.exists():
            continue

        for f in sorted(memory_dir.iterdir()):
            if f.name in EXCLUDE_FILES:
                continue
            if f.suffix != ".md":
                continue
            if f.parent.name in EXCLUDE_DIRS:
                continue
            # 跳过 bak 目录
            if f.parent.name == "bak" or "bak" in f.parts:
                continue
            files.append(f)

    return files


def main():
    parser = argparse.ArgumentParser(description="修复老记忆文件格式")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际修改")
    args = parser.parse_args()

    files = scan_memory_dirs()

    if not files:
        logger.info("没有需要修复的记忆文件")
        return

    logger.info("扫描到 %d 个记忆文件", len(files))
    logger.info("")

    total_changes = 0
    fixed_count = 0

    for f in files:
        result = fix_file(f, dry_run=args.dry_run)
        changes = result.get("changes", [])
        if changes:
            fixed_count += 1
            total_changes += len(changes)
            logger.info("  %s", f.name)
            for c in changes:
                logger.info("    ✓ %s", c)
            if "backup" in result:
                logger.info("    → 备份: %s", result["backup"])
            logger.info("")

    if args.dry_run:
        logger.info("=" * 40)
        logger.info("[DRY RUN] 共 %d 个文件需修复（%d 项改动）", fixed_count, total_changes)
        logger.info("运行不带 --dry-run 执行实际写入")
    else:
        logger.info("=" * 40)
        logger.info("完成: 修复 %d 个文件（%d 项改动）", fixed_count, total_changes)
        logger.info("备份已保存到各目录的 bak/ 下")


if __name__ == "__main__":
    main()
