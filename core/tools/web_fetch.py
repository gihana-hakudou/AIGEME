"""WebFetch — 读取网页内容（纯标准库，无浏览器依赖）

抓取指定 URL 的 HTML 页面，提取可读文本内容返回给 LLM，
无需打开浏览器。适合读取文档、文章、API 文档等静态页面。
"""

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from html.parser import HTMLParser

from core.tools.base import BaseTool

logger = logging.getLogger(__name__)

# 默认请求头
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_DEFAULT_TIMEOUT = 15  # 秒
_MAX_CONTENT_LENGTH = 8000  # 返回文本的最大字符数


class _HTMLStripper(HTMLParser):
    """去除 HTML 标签，保留文本和少量结构"""

    def __init__(self) -> None:
        super().__init__()
        self._text_parts: list[str] = []
        self._skip_newline = False

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            # 如果前一个 token 是内联标签的结束，且没有空格，则加一个空格
            if self._text_parts and not self._text_parts[-1].endswith(" "):
                self._text_parts.append(" ")
            self._text_parts.append(stripped)
            self._skip_newline = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # 块级标签后加换行
        if tag in ("p", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                   "div", "section", "blockquote", "pre", "hr"):
            if not self._skip_newline:
                self._text_parts.append("\n")
            self._skip_newline = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li",
                   "blockquote", "pre", "div", "section", "tr"):
            if not self._skip_newline:
                self._text_parts.append("\n")
            self._skip_newline = True

    def text(self) -> str:
        return "".join(self._text_parts).strip()


def _strip_html(html_text: str) -> str:
    """去除 HTML 标签，返回纯文本。"""
    stripper = _HTMLStripper()
    try:
        stripper.feed(html_text)
    except Exception:
        # HTML 解析异常时回退到正则
        pass
    result = stripper.text()
    if not result:
        # 兜底：正则去除标签
        result = re.sub(r'<[^>]+>', ' ', html_text)
        result = re.sub(r'\s+', ' ', result).strip()
    return result


def _fetch_url(url: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """获取 URL 的 HTML 内容，返回原始 HTML。

    Raises:
        ValueError: URL 格式无效。
        OSError: 网络请求失败。
    """
    # 基本 URL 校验
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"无效的 URL: {url}")

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # 尝试从 Content-Type 或 HTML <meta> 中提取编码
            content_type = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
            # 兜底探测编码
            try:
                return raw.decode(charset)
            except (LookupError, UnicodeDecodeError):
                # 依次尝试常见编码
                for enc in ("utf-8", "gbk", "gb2312", "utf-16", "shift_jis", "euc-jp"):
                    try:
                        return raw.decode(enc)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise OSError(f"HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise OSError(f"网络错误: {e.reason}") from e


class WebFetchTool(BaseTool):
    """读取网页内容工具"""

    name = "web_fetch"
    description = (
        "读取指定 URL 的网页内容，返回可读的纯文本。"
        "适合阅读文档、文章、新闻、API 文档等静态页面内容。"
        "无需打开浏览器。"
    )
    output_type = "text"

    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要读取的网页 URL（完整地址，含 http:// 或 https://）",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 15",
                "default": 15,
            },
            "max_length": {
                "type": "integer",
                "description": "返回文本的最大字符数，默认 8000。超长页面会截断并提示",
                "default": 8000,
            },
        },
        "required": ["url"],
    }

    async def execute(  # type: ignore[override]
        self,
        url: str,
        timeout: int = _DEFAULT_TIMEOUT,
        max_length: int = _MAX_CONTENT_LENGTH,
        **kwargs: Any,
    ) -> dict:
        try:
            raw_html = _fetch_url(url, timeout=timeout)
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        except OSError as e:
            return {"status": "error", "error": f"读取失败: {e}"}

        text = _strip_html(raw_html)
        title = ""
        # 尝试提取 <title>
        m = re.search(r'<title[^>]*>([^<]+)</title>', raw_html, re.IGNORECASE)
        if m:
            title = m.group(1).strip()

        truncated = False
        if len(text) > max_length:
            text = text[:max_length]
            truncated = True

        result_parts: list[str] = []
        if title:
            result_parts.append(f"标题: {title}")
        result_parts.append(f"来源: {url}")
        result_parts.append("")
        result_parts.append(text)
        if truncated:
            result_parts.append("")
            result_parts.append("(内容已截断，仅显示前 {} 字符)".format(max_length))

        return {
            "status": "ok",
            "result": "\n".join(result_parts),
        }
