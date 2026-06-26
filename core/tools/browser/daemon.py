"""CDP WS holder + IPC relay (adapted from AiGirl/MASTER).

One daemon per AIGEME_BU_NAME.  manager.py starts this as a
subprocess when the daemon is not already running.

Architecture:
  tools.py (async) → manager.py → helpers.py (sync) → IPC socket → daemon.py (async, CDPClient) → Chrome
"""

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from . import _ipc as ipc
from cdp_use.client import CDPClient

logger = logging.getLogger(__name__)

BU_NAME     = os.environ.get("AIGEME_BU_NAME", "aigeme")
BU_CDP_WS  = os.environ.get("BU_CDP_WS")
BU_CDP_URL = os.environ.get("BU_CDP_URL")
LOG_PATH    = ipc.log_path(BU_NAME)
PID_PATH    = ipc.pid_path(BU_NAME)
SOCK_ADDR   = ipc.sock_addr(BU_NAME)

INTERNAL_PREFIXES = (
    "chrome://", "chrome-untrusted://", "devtools://",
    "chrome-extension://", "about:",
)


def _log(msg: str):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{_ts()}] {msg}\n")
    except Exception:
        pass


def _ts() -> str:
    now = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)) + f".{int((now % 1) * 1000):03d}"


def get_ws_url() -> str | None:
    """尝试获取已有浏览器的 CDP WebSocket URL

    优先级：
    1. BU_CDP_WS 环境变量
    2. BU_CDP_URL 环境变量（HTTP 端点）
    3. 端口探测（检查已有浏览器）

    如果都不存在则返回 None，由调用方决定是否启动新实例。
    """
    if BU_CDP_WS:
        return BU_CDP_WS

    if BU_CDP_URL:
        base = BU_CDP_URL.rstrip("/")
        deadline = time.time() + 30
        last_err = None
        while time.time() < deadline:
            try:
                import urllib.request
                raw = urllib.request.urlopen(f"{base}/json/version", timeout=5).read()
                return json.loads(raw)["webSocketDebuggerUrl"]
            except Exception as e:
                last_err = e
                time.sleep(1)
        raise RuntimeError(f"BU_CDP_URL={BU_CDP_URL} unreachable after 30s: {last_err}")

    # 端口探测
    import urllib.request
    for port in (9222, 9223, 9333):
        try:
            raw = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1
            ).read()
            ws_url = json.loads(raw)["webSocketDebuggerUrl"]
            _log(f"Found running browser at port {port}: {ws_url}")
            return ws_url
        except Exception:
            continue

    return None


class Daemon:
    def __init__(self):
        self.cdp: CDPClient | None = None
        self.session: str | None = None
        self.target_id: str | None = None
        self.events: deque = deque(maxlen=500)
        self.dialog: dict | None = None
        self._stop: asyncio.Event = asyncio.Event()
        self._ready: bool = False
        self._pending_download: dict | None = None  # {"url": ..., "suggested_filename": ..., "path": ..., ...}
        self._download_lock = threading.Lock()  # 保护 _pending_download 线程安全
        self._mark_js = (
            r"if(!document.title.startsWith('\u{1F434}'))"
            r"document.title='\u{1F434} '+document.title"
        )
        # Patchright 资源引用（用于 shutdown 清理和 download 事件）
        self._pw = None
        self._pw_browser = None
        self._pw_page = None

    async def start(self):
        # 1. 检查已有浏览器（env var / 端口扫描）
        url = await asyncio.to_thread(get_ws_url)

        # 2. 没有已有浏览器时，用 Patchright 启动新实例（带 download 支持）
        if not url:
            url = await asyncio.to_thread(self._launch_patchright_with_download_support)

        _log(f"Connecting to {url}")
        self.cdp = CDPClient(url)
        try:
            await self.cdp.start()
        except Exception as e:
            raise RuntimeError(
                f"CDP WS handshake failed: {e} — "
                "Patchright Chromium 启动失败，请确认 patchright install chromium 已执行。"
            )
        await self._attach_first_page()

        # 标记就绪
        self._ready = True

        orig = self.cdp._event_registry.handle_event
        async def tap(method, params, session_id=None):
            self.events.append({"method": method, "params": params, "session_id": session_id})
            if method == "Page.javascriptDialogOpening":
                self.dialog = params
            elif method == "Page.javascriptDialogClosed":
                self.dialog = None
            elif method in ("Page.loadEventFired", "Page.domContentEventFired"):
                asyncio.create_task(self._mark_tab_title())
            return await orig(method, params, session_id)
        self.cdp._event_registry.handle_event = tap
        _log("Daemon started")

    def _launch_patchright_with_download_support(self, debug_port: int = 9222) -> str:
        """启动 Patchright 管理的 Chromium

        Chromium 窗口由 CDP _attach_first_page 统一管理。
        下载支持通过 CDP Browser.setDownloadBehavior 实现。
        """
        # 先导入 — PLAYWRIGHT_BROWSERS_PATH 不能在 import 时设置，会导致卡死
        from patchright.sync_api import sync_playwright

        # 导入后再设置路径，让 start() 能找到项目本地的 Chromium
        _project_root = Path(__file__).resolve().parent.parent.parent.parent
        _browser_dir = str(_project_root / ".AIGEME" / ".browser")
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _browser_dir

        pw = sync_playwright().start()
        _log("Launching Patchright-managed Chromium...")
        browser = pw.chromium.launch(
            headless=False,
            args=[
                f'--remote-debugging-port={debug_port}',
                '--no-sandbox',
                '--no-first-run',
                '--no-default-browser-check',
                # 注意: 不使用 --no-startup-window
                # 该参数阻止 Chrome 创建初始窗口，与 headless=False 冲突，
                # 会导致 browser.new_page() 创建的窗口闪退 (0.1s)
            ],
        )
        _log("Patchright Chromium launched")

        # 不要调用 browser.new_page() — 页面由 CDP _attach_first_page 统一管理
        # Patchright 的 page.on('download') 与 CDP Target 通道不兼容

        # 保持引用防止 GC
        self._pw = pw
        self._pw_browser = browser
        self._pw_page = None  # 不再由 Patchright 管理页面

        # 等待 CDP 端口可用
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                import urllib.request
                raw = urllib.request.urlopen(
                    f"http://127.0.0.1:{debug_port}/json/version", timeout=2
                ).read()
                ws_url = json.loads(raw)["webSocketDebuggerUrl"]
                _log(f"Patchright Chromium ready, WS URL: {ws_url}")
                return ws_url
            except Exception:
                time.sleep(0.5)

        raise RuntimeError(
            f"Patchright Chromium launched but DevTools not responding after 15s. "
            f"Check {LOG_PATH}"
        )

    def _on_download(self, download):
        """Patchright download 事件回调（预留，已迁移至 CDP setDownloadBehavior）

        当前下载由 Browser.setDownloadBehavior 自动处理，
        Chromium 会自动将文件保存到指定目录，无需事件回调。
        """
        try:
            download_dir = self._download_dir()
            download_dir.mkdir(parents=True, exist_ok=True)

            filename = download.suggested_filename or f"download_{int(time.time())}.bin"
            dest = str(download_dir / filename)

            # Playwright 的 save_as() 必须在下载事件触发后尽快调用
            download.save_as(dest)

            size_kb = round(Path(dest).stat().st_size / 1024, 1) if Path(dest).exists() else 0

            with self._download_lock:
                self._pending_download = {
                    "url": download.url,
                    "suggested_filename": filename,
                    "path": dest,
                    "size_kb": size_kb,
                }

            _log(f"Download saved: {filename} ({size_kb} KB) from {download.url}")
        except Exception as e:
            _log(f"Download callback error: {e}")

    async def _mark_tab_title(self):
        try:
            await asyncio.wait_for(
                self.cdp.send_raw(
                    "Runtime.evaluate",
                    {"expression": self._mark_js},
                    session_id=self.session,
                ),
                timeout=2,
            )
        except Exception:
            pass

    async def _attach_first_page(self):
        targets = (await self.cdp.send_raw("Target.getTargets"))["targetInfos"]
        pages = [t for t in targets if self._is_real_page(t)]

        if not pages:
            tid = (await self.cdp.send_raw("Target.createTarget", {"url": "about:blank"}))["targetId"]
            _log(f"No real pages, created about:blank ({tid})")
            pages = [{"targetId": tid, "url": "about:blank", "type": "page"}]

        r = await self.cdp.send_raw("Target.attachToTarget", {"targetId": pages[0]["targetId"], "flatten": True})
        self.session = r["sessionId"]
        self.target_id = pages[0]["targetId"]
        _log(f"Attached {self.target_id} ({pages[0].get('url', '')[:80]}) session={self.session}")
        await self._enable_domains(self.session)

    @staticmethod
    def _is_real_page(t: dict) -> bool:
        return (
            t["type"] == "page"
            and not any(t.get("url", "").startswith(p) for p in INTERNAL_PREFIXES)
        )

    async def _enable_domains(self, session_id: str):
        async def _enable_one(d):
            try:
                await asyncio.wait_for(
                    self.cdp.send_raw(f"{d}.enable", session_id=session_id),
                    timeout=4,
                )
            except Exception as e:
                _log(f"Enable {d} on {session_id}: {e}")

        await asyncio.gather(*(_enable_one(d) for d in ("Page", "DOM", "Runtime", "Network")))
        # 不启用 Browser domain — 某些 Chromium 版本不支持 Browser.enable (code -32601)
        # 下载行为通过 Browser.setDownloadBehavior 设置（即使 domain 未 enable 也可调用）
        try:
            await asyncio.wait_for(
                self.cdp.send_raw("Browser.setDownloadBehavior", {
                    "behavior": "allow",
                    "downloadPath": str(self._download_dir()),
                }, session_id=session_id),
                timeout=4,
            )
        except Exception as e:
            _log(f"setDownloadBehavior failed: {e}")

    def _download_dir(self) -> Path:
        """下载目录路径"""
        return (
            Path(__file__).resolve().parent.parent.parent.parent
            / ".AIGEME" / ".data" / "tmp" / "browser-control" / "downloads"
        )

    async def handle(self, req: dict) -> dict:
        expected = ipc.expected_token()
        if expected is not None and req.get("token") != expected:
            return {"error": "unauthorized"}

        meta = req.get("meta")

        if meta == "ping":
            return {"pong": True, "pid": os.getpid()}

        if meta == "ready":
            return {"ready": self._ready}

        if meta == "drain_events":
            out = list(self.events)
            self.events.clear()
            return {"events": out}

        if meta == "session":
            return {"session_id": self.session}

        if meta == "current_tab":
            if not self.target_id:
                return {"error": "not_attached"}
            try:
                info = (await self.cdp.send_raw("Target.getTargetInfo", {"targetId": self.target_id}))["targetInfo"]
                return {
                    "targetId": info.get("targetId"),
                    "url": info.get("url", ""),
                    "title": info.get("title", ""),
                }
            except Exception:
                return {"error": "cdp_disconnected"}

        if meta == "connection_status":
            if not self.target_id:
                return {"error": "not_attached"}
            try:
                info = (await self.cdp.send_raw("Target.getTargetInfo", {"targetId": self.target_id}))["targetInfo"]
                page = None
                if self._is_real_page(info):
                    page = {
                        "targetId": info.get("targetId"),
                        "title": info.get("title") or "(untitled)",
                        "url": info.get("url") or "",
                    }
                return {"target_id": self.target_id, "session_id": self.session, "page": page}
            except Exception:
                return {"error": "cdp_disconnected"}

        if meta == "set_session":
            old = self.session
            self.session = req.get("session_id")
            self.target_id = req.get("target_id") or self.target_id
            if old and old != self.session:
                asyncio.create_task(self._disable_old_session(old))
            asyncio.create_task(self._enable_domains(self.session))
            return {"session_id": self.session}

        if meta == "pending_dialog":
            return {"dialog": self.dialog}

        if meta == "shutdown":
            self._stop.set()
            return {"ok": True}

        if meta == "pending_download":
            with self._download_lock:
                info = self._pending_download
            if info:
                return {"status": "ok", "download": info}
            return {"status": "ok", "download": None}

        if meta == "clear_download":
            with self._download_lock:
                self._pending_download = None
            return {"status": "ok"}

        method = req["method"]
        params = req.get("params") or {}
        sid = (
            None if method.startswith("Target.")
            else (req.get("session_id") or self.session)
        )

        try:
            result = await asyncio.wait_for(
                self.cdp.send_raw(method, params, session_id=sid),
                timeout=10.0,
            )
            return {"result": result}
        except Exception as e:
            msg = str(e)
            if "Session with given id not found" in msg and sid == self.session and sid:
                _log(f"Stale session {sid}, re-attaching...")
                await self._attach_first_page()
                try:
                    result = await asyncio.wait_for(
                        self.cdp.send_raw(method, params, session_id=self.session),
                        timeout=10.0,
                    )
                    return {"result": result}
                except Exception as e2:
                    return {"error": str(e2)}
            return {"error": msg}

    async def _disable_old_session(self, old_session: str):
        try:
            await asyncio.wait_for(
                self.cdp.send_raw("Network.disable", session_id=old_session),
                timeout=2,
            )
        except Exception:
            pass

    async def shutdown(self):
        """优雅关闭 daemon，清理 CDP 和 Patchright 资源"""
        self._stop.set()
        if self.cdp:
            try:
                await self.cdp.close()
            except Exception:
                pass
        # 清理 Patchright 资源
        if self._pw_page is not None:
            try:
                self._pw_page.close()
            except Exception:
                pass
        if self._pw_browser:
            try:
                self._pw_browser.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        ipc.cleanup_endpoint(BU_NAME)
        _log("Daemon shut down")


async def serve_handler(daemon: Daemon, reader, writer):
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        if not line:
            return
        req = json.loads(line.decode())
        resp = await daemon.handle(req)
        writer.write((json.dumps(resp, default=str) + "\n").encode())
        await writer.drain()
    except asyncio.TimeoutError:
        try:
            writer.write((json.dumps({"error": "timeout"}) + "\n").encode())
            await writer.drain()
        except Exception:
            pass
    except Exception as e:
        _log(f"IPC handler error: {e}")
        try:
            writer.write((json.dumps({"error": str(e)}) + "\n").encode())
            await writer.drain()
        except Exception:
            pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def already_running() -> bool:
    return ipc.ping(BU_NAME, timeout=1.0)


async def main():
    if already_running():
        print(f"Daemon already running on {SOCK_ADDR}", file=sys.stderr)
        sys.exit(0)

    PID_PATH.write_text(str(os.getpid()))

    daemon = Daemon()

    async def handler(reader, writer):
        await serve_handler(daemon, reader, writer)

    serve_task = asyncio.create_task(ipc.serve(BU_NAME, handler))
    stop_task = asyncio.create_task(daemon._stop.wait())
    await asyncio.sleep(0.5)

    try:
        await daemon.start()
        done, pending = await asyncio.wait(
            {serve_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if serve_task in done:
            await serve_task
    except KeyboardInterrupt:
        pass
    except Exception as e:
        _log(f"Fatal: {e}")
        logger.error(f"Browser daemon fatal error: {e}")
        sys.exit(1)
    finally:
        for t in (serve_task, stop_task):
            if not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        await daemon.shutdown()
        try:
            PID_PATH.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
