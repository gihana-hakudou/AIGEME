"""MemoryTool mixin: 纯工具函数 + 倒排索引构建"""

import logging
import re
from datetime import datetime
from pathlib import Path

import jieba

from core.memory.yaml_handler import YamlFrontmatter
from core.tools.file_lock import LockManager

logger = logging.getLogger(__name__)


class MemoryUtilsMixin:
    """倒排索引构建、分词、日期判断等纯工具方法"""

    # 倒排索引构建版本号 — 代码变更时递增，强制所有会话重建索引
    _INVERTED_INDEX_VERSION = 2

    @staticmethod
    def _count_entries(file_path: Path) -> str:
        """统计文件中的条目数"""
        try:
            count = sum(
                1
                for line in file_path.read_text("utf-8").splitlines()
                if line.startswith("- [")
            )
            return str(count)
        except Exception:
            return "0"

    @staticmethod
    def _is_within_days(date_str: str, days: int, now: datetime) -> bool:
        """检查日期是否在指定天数内"""
        try:
            d = datetime.strptime(date_str[:10], "%Y-%m-%d") if date_str else now
            return (now - d).days <= days
        except (ValueError, IndexError):
            return True

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """中文分词：使用 jieba 提取有意义的词汇

        英文单词保持原有逻辑（提取 >=2 字符的单词），
        中文部分改用 jieba 分词，避免产生无意义双字。
        """
        words: set[str] = set()
        # 英文词
        for m in re.finditer(r"[a-zA-Z_]\w{1,}", text):
            words.add(m.group().lower())
        # 中文部分使用 jieba 分词
        chinese_text = re.sub(r"[^\u4e00-\u9fff]", "", text)
        if chinese_text:
            for word in jieba.lcut(chinese_text):
                w = word.strip()
                if len(w) >= 2:
                    words.add(w)
        return words

    async def _build_inverted_index(self, memory_dir: Path) -> dict:
        """构建 {word: {filename: {line_indices}}} 倒排索引

        去掉 YAML frontmatter 后索引所有行（包括 markdown 格式内容），逐文件加读锁
        """
        lm = await LockManager.get_instance()
        index: dict[str, dict[str, set[int]]] = {}
        for f in sorted(memory_dir.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            async with lm.acquire_read(f):
                content = f.read_text("utf-8")
            # 去掉 YAML frontmatter，只索引正文
            fm, body = YamlFrontmatter.extract_io(content)
            if not body:
                continue

            # 把 tags 也加入索引（用虚拟行号 -1 标记，只在搜索匹配时生效）
            raw_tags = fm.get("tags", []) or [] if fm else []
            tag_words: set[str] = set()
            for t in raw_tags:
                if isinstance(t, str):
                    for w in self._tokenize(t):
                        tag_words.add(w)
            for tag_word in tag_words:
                if tag_word not in index:
                    index[tag_word] = {}
                if f.name not in index[tag_word]:
                    index[tag_word][f.name] = set()
                index[tag_word][f.name].add(-1)
            for ln, line in enumerate(body.split("\n")):
                if not line.strip():
                    continue
                words = self._tokenize(line)
                for word in words:
                    if word not in index:
                        index[word] = {}
                    if f.name not in index[word]:
                        index[word][f.name] = set()
                    index[word][f.name].add(ln)
        return index
