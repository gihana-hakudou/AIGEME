"""Browser control helpers — ALL synchronous, talk to daemon via IPC.

Architecture:
  tools.py (async BaseTool) → manager.py → helpers.py (sync, IPC) → daemon.py (CDPClient) → Chrome

Adapted from AiGirl/MASTER/infrastructure/browser/helpers.py
"""

import base64
import gzip
import json
import logging
import math
import os
import sys
import time
from pathlib import Path

from . import _ipc as ipc

logger = logging.getLogger(__name__)

BU_NAME = os.environ.get("AIGEME_BU_NAME", "aigeme")
BU_DEBUG_CLICKS = os.environ.get("BU_DEBUG_CLICKS")

INTERNAL_PREFIXES = (
    "chrome://", "chrome-untrusted://", "devtools://",
    "chrome-extension://", "about:",
)

# ── 目录常量（与 tools.py 保持一致） ──────────────────────────────────────────

# 项目根目录 = helpers.py 向上 4 级：browser → tools → core → AIGEME
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SS_DIR = _PROJECT_ROOT / ".AIGEME" / ".data" / "tmp" / "img"
_DATA_DIR = _PROJECT_ROOT / ".AIGEME" / ".data" / "tmp" / "browser-control"
DOWNLOAD_DIR = _DATA_DIR / "downloads"

# 模块加载时自动创建
SS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ── Low-level IPC ──────────────────────────────────────────────────────────

def _send(req: dict) -> dict:
    sock, token = ipc.connect(BU_NAME, timeout=5.0)
    try:
        resp = ipc.request(sock, token, req)
    finally:
        sock.close()
    if "error" in resp:
        raise RuntimeError(resp["error"])
    return resp


def cdp(method: str, session_id=None, **params) -> dict:
    return _send({
        "method": method,
        "params": params,
        "session_id": session_id,
    }).get("result", {})


def drain_events() -> list:
    return _send({"meta": "drain_events"}).get("events", [])


# ── JavaScript helpers ─────────────────────────────────────────────────────

def _js_snippet(expression: str, limit: int = 160) -> str:
    s = expression.strip().replace("\n", "\\n")
    return s[:limit - 3] + "..." if len(s) > limit else s


def _js_exception_description(result: dict, details: dict | None) -> str:
    desc = result.get("description")
    if not desc and isinstance(details, dict):
        exc = details.get("exception")
        if isinstance(exc, dict):
            desc = exc.get("description")
            if desc is None and "value" in exc:
                desc = str(exc["value"])
    if desc is None and details:
        desc = details.get("text")
    return desc or "JavaScript evaluation failed"


def _decode_unserializable(value: str):
    if value == "NaN":       return math.nan
    if value == "Infinity":   return math.inf
    if value == "-Infinity":  return -math.inf
    if value == "-0":         return -0.0
    if value.endswith("n"):    return int(value[:-1])
    return value


def _runtime_value(response: dict, expression: str) -> object:
    result = response.get("result", {})
    details = response.get("exceptionDetails")
    if details or result.get("subtype") == "error":
        desc = _js_exception_description(result, details)
        loc = ""
        if details:
            ln = details.get("lineNumber")
            col = details.get("columnNumber")
            if ln is not None and col is not None:
                loc = f" at line {ln}, column {col}"
        raise RuntimeError(
            f"JavaScript evaluation failed{loc}: {desc}; expression: {_js_snippet(expression)}"
        )
    if "value" in result:
        val = result["value"]
        return _decode_unserializable(val) if isinstance(val, str) else val
    return None


def _runtime_evaluate(expression: str, session_id=None, await_promise=False):
    params = {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": await_promise,
    }
    if session_id:
        params["session_id"] = session_id
    return cdp("Runtime.evaluate", **params)


def _has_return_statement(expression: str) -> bool:
    stripped = expression.strip()
    if stripped.startswith("return "):
        return True
    # 多行代码中检查顶层 return
    for line in stripped.split("\n"):
        if line.strip().startswith("return "):
            return True
    return False


# ── User-facing helpers ────────────────────────────────────────────────────

def js(expression: str, target_id=None):
    """通过 CDP 执行 JavaScript 并返回结果。
    
    Runtime.evaluate 会自动返回表达式的值，不要加 return 语句（顶层 return 会报错）。
    多行 JS 可以用 IIFE 包装：(function(){ ... })()
    """
    expr = expression.strip()
    response = _runtime_evaluate(expr)
    return _runtime_value(response, expression)


def _mark_tab():
    try:
        js(r"if(!document.title.startsWith('\u{1F434}'))document.title='\u{1F434} '+document.title")
    except Exception:
        pass


def goto_url(url: str):
    """跳转到指定 URL"""
    if not url.startswith(("http://", "https://", "file://", "about:")):
        url = "https://" + url
    cdp("Page.navigate", url=url)


def search_baidu(query: str):
    """在百度搜索（默认搜索引擎）"""
    # 百度搜索 URL 编码
    from urllib.parse import quote
    encoded = quote(query)
    cdp("Page.navigate", url=f"https://www.baidu.com/s?wd={encoded}&tn=SE_baiduhome_pg")


def page_info() -> dict:
    """获取当前页面信息 {url, title, w, h, sx, sy, pw, ph, dialog}"""
    result = {
        "url": "",
        "title": "",
        "w": 0,  "h": 0,
        "sx": 0, "sy": 0,
        "pw": 0, "ph": 0,
    }
    try:
        result["url"] = js("document.location.href")
    except Exception:
        pass
    try:
        result["title"] = js("document.title")
    except Exception:
        pass
    try:
        metrics = cdp("Page.getLayoutMetrics")
        cv = metrics.get("cssVisualViewport", {})
        cs = metrics.get("cssContentSize", {})
        result["w"] = int(cv.get("clientWidth", 0))
        result["h"] = int(cv.get("clientHeight", 0))
        result["sx"] = int(cv.get("scrollX", 0))
        result["sy"] = int(cv.get("scrollY", 0))
        result["pw"] = int(cs.get("width", 0))
        result["ph"] = int(cs.get("height", 0))
    except Exception:
        pass
    try:
        diag = _send({"meta": "pending_dialog"}).get("dialog")
        if diag:
            result["dialog"] = diag
    except Exception:
        pass
    return result


def click_at_xy(x: int, y: int, button: str = "left", clicks: int = 1):
    """点击屏幕坐标 (x, y)"""
    btn_map = {"left": 0, "middle": 1, "right": 2, "back": 3, "forward": 4}
    btn = btn_map.get(button, 0)
    cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button=button, buttons=1, clickCount=clicks)
    cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button=button, buttons=0, clickCount=clicks)
    if BU_DEBUG_CLICKS:
        logger.info(f"[click] ({x},{y}) button={button}")


def type_text(text: str):
    """在当前焦点元素上输入文本（不先聚焦）"""
    cdp("Input.insertText", text=text)


def fill_input(selector: str, text: str, clear_first: bool = True,
               timeout: float = 0):
    """通过选择器填充输入框"""
    if timeout > 0:
        wait_for_element(selector, timeout=timeout)
    escaped = json.dumps(selector)[1:-1]
    if clear_first:
        js(f"""
        (() => {{
            const el = document.querySelector('{escaped}');
            if (!el) throw new Error('Selector not found: {escaped}');
            el.focus();
            el.select();
            el.value = '';
            return true;
        }})()
        """)
    else:
        js(f"""
        (() => {{
            const el = document.querySelector('{escaped}');
            if (!el) throw new Error('Selector not found: {escaped}');
            el.focus();
            return true;
        }})()
        """)
    encoded_key = "".join(f"\\u{ord(c):04X}" for c in text)
    cdp("Input.insertText", text=text)


def press_key(key: str, modifiers: int = 0):
    """按键（Enter/Tab/Backspace/Escape/ArrowDown 等）
    修饰位：1=Alt, 2=Ctrl, 4=Meta(Cmd), 8=Shift
    """
    # 修饰键参数定义
    modifier_flags = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8}
    modifiers_val = modifier_flags.get(modifiers, modifiers)
    cdp("Input.dispatchKeyEvent", type="rawKeyDown", windowsVirtualKeyCode=0, key=key, modifiers=modifiers_val)
    cdp("Input.dispatchKeyEvent", type="keyUp", windowsVirtualKeyCode=0, key=key, modifiers=modifiers_val)


def dispatch_key(selector: str, key: str = "Enter", event: str = "keypress"):
    """向选择器匹配的元素发送 DOM 键盘事件"""
    escaped = json.dumps(selector)[1:-1]
    js(f"""
    (() => {{
        const el = document.querySelector('{escaped}');
        if (!el) throw new Error('Element not found: {escaped}');
        el.dispatchEvent(new KeyboardEvent('{event}', {{ key: '{key}', bubbles: true }}));
        return true;
    }})()
    """)


def scroll(x: int = 0, y: int = 0, dy: int = -300, dx: int = 0):
    """滚动页面（负数 dy=向下，正数 dy=向上）"""
    sx = dx if dx != 0 else x
    sy = dy if dy != 0 else y
    cdp("Input.dispatchMouseEvent", type="mouseWheel", x=x or 100, y=y or 100,
        deltaX=float(sx), deltaY=float(sy))


def capture_screenshot(path: str | None = None,
                       full: bool = False, max_dim: int | None = None) -> dict:
    """保存截图，返回包含文件路径和 data_url 的 dict

    返回值:
        dict: {path, data_url, width, height, size_kb}
        - path: 文件绝对路径
        - data_url: data:image/png;base64,... 格式用于 LLM 多模态注入
        - width / height: 图片尺寸（像素）
        - size_kb: 图片大小（KB）

    向下兼容: result["path"] 获取文件路径；旧代码如果直接预期字符串，
              请改用 result["path"] 访问文件路径
    """
    if full:
        result = cdp("Page.captureScreenshot", format="png", fullPage=True)
    else:
        result = cdp("Page.captureScreenshot", format="png")
    data = result.get("data", "")
    if not data:
        # 可能返回的是 base64-encoded 数据
        data = result.get("dataURL", "").removeprefix("data:image/png;base64,")

    if path is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = str(SS_DIR / f"browser_ss_{ts}.png")

    raw = base64.b64decode(data)
    
    # 如果 max_dim 指定，缩放图片
    if max_dim:
        try:
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(raw))
            w, h = img.size
            if w > max_dim or h > max_dim:
                ratio = min(max_dim / w, max_dim / h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
                buf = BytesIO()
                img.save(buf, format="PNG")
                raw = buf.getvalue()
        except ImportError:
            pass  # PIL 不可用，保持原样

    Path(path).write_bytes(raw)
    abs_path = os.path.abspath(path)

    # 构造返回 dict
    b64 = base64.b64encode(raw).decode()
    data_url = f"data:image/png;base64,{b64}"

    # 读取图片尺寸
    width = 0
    height = 0
    try:
        from PIL import Image
        from io import BytesIO
        img = Image.open(BytesIO(raw))
        width, height = img.size
    except ImportError:
        pass

    return {
        "path": abs_path,
        "data_url": data_url,
        "width": width,
        "height": height,
        "size_kb": round(len(raw) / 1024, 1),
    }


def setup_download_handler(path: str | None = None) -> dict:
    """配置下载目录

    下载由 daemon 中 Patchright 的 page.on('download') 事件自动处理，
    文件保存到 DOWNLOAD_DIR。此函数仅用于确保下载目录存在。

    Args:
        path: 下载目录路径，默认 DOWNLOAD_DIR

    Returns:
        {"status": "ok", "download_dir": "..."}
    """
    target = Path(path or str(DOWNLOAD_DIR))
    target.mkdir(parents=True, exist_ok=True)
    return {"status": "ok", "download_dir": str(target)}


def pending_download() -> dict | None:
    """获取浏览器待处理的下载信息

    返回 daemon 中 Patchright 自动保存的下载信息：
        {"url": "https://...", "suggested_filename": "file.zip", "path": "...", "size_kb": ...}
    如果没有待处理的下载，返回 None。
    """
    resp = _send({"meta": "pending_download"})
    return resp.get("download")


def accept_download(timeout: float = 30.0) -> dict:
    """等待并接受浏览器下载，保存到 DOWNLOAD_DIR

    依赖 daemon 中 Patchright 的 page.on('download') 事件，
    下载文件由 daemon 自动保存到 DOWNLOAD_DIR，helper 只需查询结果。

    Args:
        timeout: 等待下载事件的最大秒数

    Returns:
        {"path": ..., "filename": ..., "size_kb": ..., "url": ...} 或 {"error": ...}
    """
    deadline = time.time() + timeout

    while time.time() < deadline:
        resp = _send({"meta": "pending_download"})
        info = resp.get("download")
        if info and info.get("path"):
            _send({"meta": "clear_download"})
            return {
                "status": "ok",
                "path": info["path"],
                "filename": info.get("suggested_filename", ""),
                "size_kb": info.get("size_kb", 0),
                "url": info.get("url", ""),
            }
        time.sleep(0.3)

    return {"error": f"等待下载超时 ({timeout}s)"}


def list_tabs(include_chrome: bool = True) -> list[dict]:
    """列出所有标签页"""
    targets = cdp("Target.getTargets").get("targetInfos", [])
    if not include_chrome:
        targets = [t for t in targets if not t.get("url", "").startswith(INTERNAL_PREFIXES)]
    return targets


def current_tab() -> dict:
    """获取当前标签页信息"""
    return _send({"meta": "current_tab"})


def switch_tab(target):
    """切换到指定标签页（接受 targetId 字符串或 target dict）"""
    target_id = target if isinstance(target, str) else target.get("targetId", "")
    if not target_id:
        raise ValueError("switch_tab: target_id required")
    result = cdp("Target.attachToTarget", targetId=target_id, flatten=True)
    session_id = result.get("sessionId", "")
    _send({
        "meta": "set_session",
        "session_id": session_id,
        "target_id": target_id,
    })
    _mark_tab()


def new_tab(url: str = "about:blank") -> str:
    """打开新标签页，返回 targetId"""
    result = cdp("Target.createTarget", url=url)
    target_id = result.get("targetId", "")
    return target_id


def close_tab(target=None):
    """关闭标签页"""
    if target is None:
        info = current_tab()
        target_id = info.get("targetId", "")
    else:
        target_id = target if isinstance(target, str) else target.get("targetId", "")
    if target_id:
        cdp("Target.closeTarget", targetId=target_id)


def ensure_real_tab():
    """切换到真实（非内部）标签页"""
    info = _send({"meta": "connection_status"})
    page = info.get("page")
    if page:
        return page
    # 当前标签页无效，重新附加
    targets = list_tabs(include_chrome=False)
    if targets:
        switch_tab(targets[0])
        return current_tab()
    raise RuntimeError("没有可用的真实标签页")


def iframe_target(url_substr: str):
    """查找 iframe 目标 ID"""
    targets = cdp("Target.getTargets").get("targetInfos", [])
    for t in targets:
        if t.get("type") == "iframe" and url_substr in t.get("url", ""):
            return t
    return None


def wait(seconds: float = 1.0):
    """等待 N 秒"""
    time.sleep(seconds)


def wait_for_load(timeout: float = 15.0) -> bool:
    """等待页面加载完成"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = js("document.readyState")
            if state == "complete":
                return True
        except Exception:
            pass
        time.sleep(0.3)
    logger.warning(f"wait_for_load timeout after {timeout}s")
    return False


def wait_for_element(selector: str, timeout: float = 10.0,
                     visible: bool = False):
    """等待元素出现"""
    deadline = time.time() + timeout
    escaped = json.dumps(selector)[1:-1]
    while time.time() < deadline:
        try:
            present = js(f"document.querySelector('{escaped}') !== null")
            if present:
                if not visible:
                    return True
                vis = js(f"""
                (() => {{
                    const el = document.querySelector('{escaped}');
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                }})()
                """)
                if vis:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"wait_for_element('{selector}') timeout after {timeout}s")


def wait_for_network_idle(timeout: float = 10.0, idle_ms: int = 500) -> bool:
    """等待网络空闲"""
    deadline = time.time() + timeout
    last_activity = time.time()

    def _check():
        nonlocal last_activity
        try:
            events = drain_events()
            for ev in events:
                if ev.get("method", "").startswith("Network."):
                    last_activity = time.time()
                    return False
        except Exception:
            pass
        return (time.time() - last_activity) >= (idle_ms / 1000.0)

    while time.time() < deadline:
        if _check():
            return True
        time.sleep(0.1)
    logger.warning(f"wait_for_network_idle timeout after {timeout}s")
    return False


def upload_file(selector: str, path: str):
    """上传文件到文件输入框"""
    abs_path = os.path.abspath(path)
    escaped = json.dumps(selector)[1:-1]
    js(f"""
    (() => {{
        const el = document.querySelector('{escaped}');
        if (!el) throw new Error('Upload input not found');
        const dt = new DataTransfer();
        dt.items.add(new File([''], '{json.dumps(abs_path)[1:-1]}'));
        el.files = dt.files;
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return true;
    }})()
    """)


def http_get(url: str, headers=None, timeout: float = 20.0) -> str:
    """纯 HTTP GET 下载网页 HTML（不使用浏览器）
    
    比浏览器导航更快，适合批量抓取搜索结果中的链接内容。
    返回 HTML 字符串，可以用 re 或简单字符串匹配提取信息。
    """
    import urllib.request
    req = urllib.request.Request(url)
    # 设置默认 User-Agent 避免被网站拦截
    req.add_header("User-Agent", (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ))
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        # 自动检测编码
        charset = resp.headers.get_content_charset()
        if charset:
            return data.decode(charset, errors="replace")
        return data.decode("utf-8", errors="replace")


def handle_dialog(accept: bool = True):
    """接受或拒绝 JavaScript 对话框"""
    cdp("Page.handleJavaScriptDialog", accept=accept)


def back():
    """浏览器历史后退"""
    cdp("Page.navigate", url="javascript:history.back()")


def forward():
    """浏览器历史前进"""
    cdp("Page.navigate", url="javascript:history.forward()")


# ── Skill system (adapted, simplified) ─────────────────────────────────────

def load_agent_helpers():
    """加载 agent-workspace/agent_helpers.py（如存在）"""
    import importlib.util
    ws_path = os.environ.get("AIGEME_BU_AGENT_WORKSPACE", "")
    if not ws_path:
        return
    helper_path = Path(ws_path) / "agent_helpers.py"
    if not helper_path.exists():
        return
    spec = importlib.util.spec_from_file_location("browser_agent_helpers", str(helper_path))
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        logger.info(f"Loaded agent helpers from {helper_path}")
