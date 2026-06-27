"""Browser Control — Python 代码执行工具

核心工具 `browser_execute` 接收 Python 代码，以浏览器 helper 函数作为全局变量执行。
LLM 可以一口气写多步操作（搜索→打开页面→提取内容→截图），全部在一次调用中完成。

使用方式（LLM 调用的 Python 代码示例）：
    goto_url("https://www.baidu.com")
    wait_for_load()
    search_baidu("原神 最新版本")
    wait_for_load()
    page = page_info()
    text = js("document.body.innerText")
    ss_path = capture_screenshot()
    print(f"Title: {page['title']}")
    print(f"Content length: {len(text)}")
"""

import io
import logging
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr
from typing import Any

from core.tools.base import BaseTool
from core.tools.browser.manager import get_manager

logger = logging.getLogger(__name__)


def _ensure_browser():
    mgr = get_manager()
    mgr.start()


def _import_helpers():
    """导入所有浏览器 helper 函数作为可执行全局变量"""
    from core.tools.browser import helpers as h
    return {k: v for k, v in vars(h).items() if callable(v) and not k.startswith("_")}


class BrowserExecuteTool(BaseTool):
    """浏览器控制 — 执行 Python 代码操作浏览器（多步骤组合）

    Skill 文档提供完整函数列表，可使用 skill(use, 'browser-control') 查看可用函数。
    """

    name = "browser_execute"
    description = (
        "通过 Python 代码控制浏览器。传入 Python 代码执行浏览器操作，"
        "支持搜索、导航、截图、提取内容、点击等。\n"
        "可用函数列表：使用 skill(use, 'browser-control') 查看。\n"
        "print() 输出结果，所有函数无需 import。\n"
        "下载文件请用 download_dir 变量指向的目录。"
    )
    output_type = "json"

    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python 代码。可用函数：使用 skill(use, 'browser-control') 查看。"
                ),
            },
            "timeout": {
                "type": "number",
                "description": "代码执行超时秒数，默认 30",
                "default": 30,
            },
        },
        "required": ["code"],
    }

    async def execute(self, code: str, timeout: int = 30, **kwargs: Any) -> dict:
        _ensure_browser()

        import base64
        import time
        from pathlib import Path
        # 截图存到 character_data/browser/tmp 目录（与 helpers.py 保持一致）
        _ss_dir = Path(__file__).resolve().parent.parent.parent.parent / "character_data" / "browser" / "tmp"
        _ss_dir.mkdir(parents=True, exist_ok=True)
        _taken_screenshots: list[str] = []

        # 下载目录
        _download_dir = _ss_dir / "downloads"
        _download_dir.mkdir(parents=True, exist_ok=True)

        # 包装 capture_screenshot 以记录截图路径并重定向到 tmp 目录
        from core.tools.browser import helpers as h
        _orig_ss = h.capture_screenshot
        def _tracked_ss(path=None, full=False, max_dim=None):
            if path is None:
                path = str(_ss_dir / f"browser_ss_{time.strftime('%Y%m%d_%H%M%S')}.png")
            result = _orig_ss(path, full=full, max_dim=max_dim)
            _taken_screenshots.append(path)
            return result

        helpers = _import_helpers()
        globals_dict = helpers.copy()
        # 限制内置函数，防止 prompt injection 通过 exec() 执行危险操作
        globals_dict["__builtins__"] = {
            'True': True, 'False': False, 'None': None,
            'len': len, 'range': range, 'int': int, 'float': float,
            'str': str, 'bool': bool, 'list': list, 'dict': dict,
            'tuple': tuple, 'set': set, 'type': type,
            'isinstance': isinstance, 'issubclass': issubclass,
            'enumerate': enumerate, 'zip': zip, 'map': map, 'filter': filter,
            'max': max, 'min': min, 'sum': sum, 'abs': abs,
            'sorted': sorted, 'reversed': reversed,
            'round': round, 'print': print, 'format': format,
            'Exception': Exception, 'ValueError': ValueError,
            'TypeError': TypeError, 'KeyError': KeyError,
            'IndexError': IndexError, 'RuntimeError': RuntimeError,
            'StopIteration': StopIteration, 'StopAsyncIteration': StopAsyncIteration,
            'bytes': bytes, 'bytearray': bytearray, 'memoryview': memoryview,
            'iter': iter, 'next': next, 'id': id, 'hash': hash,
            'any': any, 'all': all, 'callable': callable,
            'hasattr': hasattr,  # 限制 hasattr（配合 helpers 使用）
            'ord': ord, 'chr': chr, 'hex': hex, 'oct': oct, 'bin': bin,
            'repr': repr, 'ascii': ascii,
            'input': input,  # 允许输入（在 exec 中不常使用但无害）
        }
        globals_dict["capture_screenshot"] = _tracked_ss
        globals_dict["download_dir"] = str(_download_dir)

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        try:
            import asyncio
            loop = asyncio.get_event_loop()

            def _run():
                with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                    exec(code, globals_dict)

            await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=timeout,
            )

            stdout = stdout_buf.getvalue()
            stderr = stderr_buf.getvalue()

            result = {
                "stdout": stdout[:10000],
                "stderr": stderr[:2000] if stderr else "",
                "truncated": len(stdout) > 10000 or len(stderr) > 2000,
            }

            # ── 截图处理：读取尺寸并转为 data_url 供多模态注入 ──
            if _taken_screenshots:
                try:
                    from PIL import Image as PILImage
                    ss_path = _taken_screenshots[0]
                    ss_data = Path(ss_path).read_bytes()

                    # 读取图片尺寸
                    with PILImage.open(ss_path) as img:
                        w, h = img.size

                    b64 = base64.b64encode(ss_data).decode()
                    result["data_url"] = f"data:image/png;base64,{b64}"
                    result["screenshot_path"] = ss_path
                    result["width"] = w
                    result["height"] = h
                    result["size_kb"] = round(len(ss_data) / 1024, 1)
                    result["file"] = ss_path
                except Exception:
                    pass

            has_screenshot = "data_url" in result
            result_dict = {
                "status": "ok",
                "result": result,
                "output_type": "image" if has_screenshot else "json",
            }
            if has_screenshot and "data_url" in result:
                result_dict["data_url"] = result["data_url"]  # 提升到顶层，供 loop.py L680 直接检查
            return result_dict

        except asyncio.TimeoutError:
            return {
                "status": "error",
                "error": f"代码执行超时（{timeout}s），请简化操作或分步执行",
                "error_type": "execution_error",
            }
        except Exception as e:
            tb = traceback.format_exc()
            code_lines = code.split("\n")
            err_line = 0
            if "line " in tb:
                try:
                    parts = tb.rsplit("line ", 1)[1]
                    err_line = int(parts.split(",")[0].strip())
                except (ValueError, IndexError):
                    pass
            error_info = f"代码执行错误: {e!s}\n\n【提交的代码】\n"
            for i, line in enumerate(code_lines, 1):
                marker = " >>>" if i == err_line else "    "
                error_info += f"{marker} {i:3d}| {line}\n"
            error_info += f"\n【Traceback】\n{tb[:2000]}"

            # 常见错误修正提示
            hints = []
            if "eval(" in code:
                hints.append("- js() 已经返回 Python 对象（list/dict/str），不要再用 eval/json.loads 解析")
            if ".decode(" in code:
                hints.append("- http_get() 已经返回字符串，不要调用 .decode()")
            if "await " in code:
                hints.append("- 所有 helper 函数都是同步的，不要加 await")
            if "JSON.parse" in code:
                hints.append("- js() 返回的是 Python 对象，不是 JSON 字符串，不要用 JSON.parse")
            if "import " in code and "re" in code and "re." not in code:
                pass  # import re 是正常的
            if hints:
                error_info += "\n💡 修正建议:\n" + "\n".join(hints)

            return {
                "status": "error",
                "error": error_info,
                "error_type": "execution_error",
            }


class BrowserSearchTool(BaseTool):
    """浏览器搜索 — 快速在百度搜索并返回结果（单步快捷操作）"""

    name = "browser_search"
    description = "在百度搜索关键词，返回搜索结果（标题+摘要）。适合快速查资讯。复杂操作请用 browser_execute。"
    output_type = "json"

    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大返回结果数", "default": 10},
        },
        "required": ["query"],
    }

    async def execute(self, query: str, max_results: int = 10, **kwargs: Any) -> dict:
        _ensure_browser()
        from core.tools.browser import helpers as h
        try:
            h.search_baidu(query)
            h.wait_for_load(timeout=15)
            h.wait(0.5)

            info = h.page_info()

            # 只在 #content_left 容器内提取搜索结果，避免抓到导航栏
            raw = h.js(r"""
            (function() {
              var c = document.getElementById('content_left');
              return c ? c.innerText : document.body.innerText;
            })()
            """)

            links = h.js(r"""
            (function() {
              var c = document.getElementById('content_left');
              if (!c) return [];
              var h3s = c.querySelectorAll('h3');
              var out = [];
              for (var i = 0; i < h3s.length && i < 20; i++) {
                var a = h3s[i].querySelector('a');
                if (!a) continue;
                var t = (a.textContent || '').trim();
                if (t && t.length > 2) {
                  out.push({ title: t, url: a.href });
                }
              }
              return out;
            })()
            """)

            result_data = {
                "query": query,
                "page_title": info.get("title", ""),
                "page_url": info.get("url", ""),
                "results": links[:max_results] if links and len(links) > 0 else [],
                "raw_text": (raw or "")[:3000],
                "raw_text_length": len(raw or ""),
                "source": "baidu",
            }

            return {
                "status": "ok",
                "result": result_data,
                "output_type": "json",
            }
        except Exception as e:
            return {"status": "error", "error": f"搜索失败: {e!s}", "error_type": "execution_error"}


class BrowserExtractTool(BaseTool):
    """内容提取 — 快速提取当前页面正文（单步快捷操作）"""

    name = "browser_extract"
    description = "提取当前浏览器页面的文字内容。复杂操作请用 browser_execute。"
    output_type = "json"

    parameters = {
        "type": "object",
        "properties": {
            "max_length": {"type": "integer", "description": "最大字符数", "default": 5000},
        },
    }

    async def execute(self, max_length: int = 5000, **kwargs: Any) -> dict:
        _ensure_browser()
        from core.tools.browser import helpers as h
        try:
            info = h.page_info()
            text = h.js("document.body.innerText")
            if not isinstance(text, str):
                text = str(text or "")
            return {
                "status": "ok",
                "result": {
                    "url": info.get("url", ""),
                    "title": info.get("title", ""),
                    "content": text[:max_length],
                    "truncated": len(text) > max_length,
                },
                "output_type": "json",
            }
        except Exception as e:
            return {"status": "error", "error": f"提取失败: {e!s}", "error_type": "execution_error"}
