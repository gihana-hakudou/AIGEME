"""重命名含 [] 的记忆文件 → 去掉方括号

[event] 猪猪因天热不理人.md  →  event 猪猪因天热不理人.md
[fact] 用户画像与偏好.md      →  fact 用户画像与偏好.md
[process] 突破 Danbooru ...   →  process 突破 Danbooru ...

同时更新:
- 所有记忆文件正文中的 [[旧文件名]] → [[新文件名]]
- LINKS.md 中的 [[旧文件名]] → [[新文件名]]
- MEMORY.md 索引表中的文件名

用法:
  python scripts/rename_bracket_files.py [--dry-run]
"""

import argparse
import logging
import re
import shutil
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / ".AIGEME" / ".data" / "local"
EXCLUDE_DIRS = {"reminders"}
EXCLUDE_FILES = {"MEMORY.md", "LINKS.md"}

# 匹配 [[文件名]] 维基链接（只处理含方括号的老文件名）
# 旧文件名匹配: [type] xxx 模式
BRACKET_FILE_RE = re.compile(r"\[(\w+)\]\s(.+)")


def scan_memory_files() -> list[Path]:
    """扫描所有记忆文件（排除 MEMORY.md/LINKS.md/reminders/bak）"""
    files = []
    for char_dir in DATA_DIR.iterdir():
        if not char_dir.is_dir():
            continue
        for sub in ["memory", "memory/_archive"]:
            memory_dir = char_dir / sub
            if not memory_dir.exists():
                continue
            for f in sorted(memory_dir.iterdir()):
                if f.name in EXCLUDE_FILES or f.parent.name in EXCLUDE_DIRS:
                    continue
                if f.suffix != ".md":
                    continue
                if f.parent.name == "bak" or "bak" in f.parts:
                    continue
                files.append(f)
    return files


def rename_needed(filepath: Path) -> str | None:
    """检查文件是否需要重命名，返回新文件名（不含.md）或 None"""
    stem = filepath.stem
    m = BRACKET_FILE_RE.match(stem)
    if m:
        _type = m.group(1)
        _rest = m.group(2).strip()
        # [event] 猪猪因天热不理人 → event猪猪因天热不理人
        new = f"{_type}{_rest}"
    elif " " in stem:
        # Muku 批量下载脚本 → Muku批量下载脚本（LINKS.md 无空格）
        new = stem.replace(" ", "")
    else:
        return None

    # 去掉所有空格（LINKS.md 存储时无空格）
    new = new.replace(" ", "")
    if new == stem:
        return None
    return new


def update_wikilinks(content: str, old_stem: str, new_stem: str) -> str:
    """替换正文中的 [[旧文件名]] → [[新文件名]]"""
    return content.replace(f"[[{old_stem}]]", f"[[{new_stem}]]")


def main():
    parser = argparse.ArgumentParser(description="重命名含方括号的记忆文件")
    parser.add_argument("--dry-run", action="store_true", help="仅预览")
    args = parser.parse_args()

    all_files = scan_memory_files()

    # 找出需要重命名的文件
    rename_map: dict[Path, str] = {}  # {旧路径: 新stem}
    for f in all_files:
        new_stem = rename_needed(f)
        if new_stem:
            rename_map[f] = new_stem

    if not rename_map:
        logger.info("没有需要重命名的文件")
        return

    logger.info("发现 %d 个需重命名的文件:\n", len(rename_map))
    for old_path, new_stem in sorted(rename_map.items()):
        logger.info("  %s", old_path.name)
        logger.info("    → %s.md\n", new_stem)

    if args.dry_run:
        logger.info("=" * 50)
        logger.info("[DRY RUN] 运行不带 --dry-run 执行实际重命名")
        return

    # 构建新旧文件名对照（旧stem → 新stem）
    stem_map: dict[str, str] = {}
    for old_path, new_stem in rename_map.items():
        stem_map[old_path.stem] = new_stem

    # 1. 更新所有文件中的 [[旧文件名]] → [[新文件名]]
    ref_updated = 0
    for f in all_files:
        content = f.read_text("utf-8")
        new_content = content
        for old_stem, new_stem in stem_map.items():
            if old_stem in new_content:
                new_content = update_wikilinks(new_content, old_stem, new_stem)
        if new_content != content:
            # 备份
            bak_dir = f.parent / "bak"
            bak_dir.mkdir(parents=True, exist_ok=True)
            bak_path = bak_dir / f"{f.name}.bak"
            if not bak_path.exists():
                shutil.copy2(f, bak_path)
            f.write_text(new_content, encoding="utf-8")
            ref_updated += 1

    if ref_updated:
        logger.info("更新 %d 个文件中的 [[...]] 引用", ref_updated)

    # 2. 重命名文件
    renamed = 0
    for old_path, new_stem in rename_map.items():
        new_path = old_path.with_stem(new_stem)
        if new_path.exists():
            logger.warning("[SKIP] 目标文件已存在: %s", new_path.name)
            continue
        # 备份
        bak_dir = old_path.parent / "bak"
        bak_dir.mkdir(parents=True, exist_ok=True)
        bak_path = bak_dir / f"{old_path.name}.bak"
        if not bak_path.exists():
            shutil.copy2(old_path, bak_path)
        # 重命名
        old_path.rename(new_path)
        renamed += 1
        logger.info("  %s → %s", old_path.name, new_path.name)

    logger.info("")
    logger.info("=" * 50)
    logger.info("完成: 重命名 %d 个文件，更新 %d 个引用文件", renamed, ref_updated)


if __name__ == "__main__":
    main()
