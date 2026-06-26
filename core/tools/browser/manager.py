"""BrowserManager — 浏览器 daemon 生命周期管理 (Singleton)"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from . import _ipc as ipc

logger = logging.getLogger(__name__)

BU_NAME = "aigeme"
_MODULE = "core.tools.browser.daemon"
_MAX_WAIT = 20.0  # 给 Chrome 启动留足时间

# Chromium 安装到项目本地，而非用户 AppData
# 项目根目录 = manager.py 向上 4 级：browser → tools → core → AIGEME
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BROWSER_DIR = _PROJECT_ROOT / ".AIGEME" / ".browser"
_PW_BROWSERS_PATH = str(_BROWSER_DIR)  # PLAYWRIGHT_BROWSERS_PATH 值


def _ensure_chromium_installed() -> bool:
    """确保 Patchright Chromium 已下载到项目本地

    检查 {@link _BROWSER_DIR}/chromium-* 是否存在。
    如果不存在，自动执行 patchright install chromium（指定 PLAYWRIGHT_BROWSERS_PATH）。

    Returns:
        True 表示已安装或安装成功，False 表示安装失败
    """
    if any(_BROWSER_DIR.glob("chromium-*")):
        return True
        logger.info("Patchright Chromium 未安装，正在自动下载（~150MB）到 %s ...", _BROWSER_DIR)
        try:
            env = os.environ.copy()
            env["PLAYWRIGHT_BROWSERS_PATH"] = _PW_BROWSERS_PATH
            subprocess.run(
                [sys.executable, "-m", "patchright", "install", "chromium"],
                check=True,
                timeout=300,  # 5min 超时
                env=env,
            )
            logger.info("Patchright Chromium 下载完成")
            return True
        except subprocess.TimeoutExpired:
            logger.error("Chromium 下载超时（>5min），请检查网络")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"Chromium 下载失败: {e}")
            return False

    return True


class BrowserManager:
    """Chrome CDP 浏览器 daemon 管理器（单例），管理 daemon 生命周期。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._proc: subprocess.Popen | None = None
        self._auto_launch_browser = True
        logger.info("BrowserManager initialized")

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """检查 daemon 是否在运行（仅 IPC 层面）"""
        return ipc.ping(BU_NAME, timeout=1.0)

    def is_ready(self) -> bool:
        """检查 daemon 是否完全就绪（CDP 已连接）"""
        try:
            import socket
            sock, token = ipc.connect(BU_NAME, timeout=2.0)
            try:
                resp = ipc.request(sock, token, {"meta": "ready"})
                return resp.get("ready", False)
            finally:
                sock.close()
        except Exception:
            return False

    def start(self) -> None:
        """启动 daemon 并等待 CDP 就绪"""
        if self.is_ready():
            logger.info("Browser daemon already ready")
            return

        # Phase 1: 确保 Patchright Chromium 已安装
        if not _ensure_chromium_installed():
            raise RuntimeError("无法自动下载 Patchright Chromium，请检查网络后重试")

        if not self.is_running():
            env = os.environ.copy()
            env.setdefault("AIGEME_BU_NAME", BU_NAME)
            # 注意：不传 PLAYWRIGHT_BROWSERS_PATH — Patchright 在 import 时读到它会卡死
            # daemon 内部的 _launch_patchright 会在导入后再设置

            logger.info(f"Starting browser daemon: {sys.executable} -m {_MODULE}")
            self._proc = subprocess.Popen(
                [sys.executable, "-m", _MODULE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                **ipc.spawn_kwargs(),  # 解耦子进程，防止 CLI 退出时 daemon 被终止
            )

        # 第一阶段：等待 IPC 启动
        deadline = time.time() + _MAX_WAIT
        while time.time() < deadline:
            if self.is_running():
                logger.info("Browser daemon IPC ready, waiting for CDP...")
                break
            time.sleep(0.3)
        else:
            raise RuntimeError(
                f"Browser daemon IPC did not start within {_MAX_WAIT}s. "
                f"Check {ipc.log_path(BU_NAME)} for details."
            )

        # 第二阶段：等待 CDP 就绪（Chrome 连接完成）
        while time.time() < deadline:
            if self.is_ready():
                logger.info("Browser daemon fully ready (CDP connected)")
                return
            time.sleep(0.3)

        raise RuntimeError(
            f"Browser daemon CDP did not become ready within {_MAX_WAIT}s. "
            f"Check {ipc.log_path(BU_NAME)} for details. "
            "Make sure Chrome is running with --remote-debugging-port=9222"
        )

    def stop(self) -> None:
        """停止 daemon"""
        try:
            from . import helpers as h
            h._send({"meta": "shutdown"})
        except Exception:
            pass

        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

        ipc.cleanup_endpoint(BU_NAME)
        logger.info("Browser daemon stopped")

    def restart(self) -> None:
        """重启 daemon"""
        self.stop()
        time.sleep(0.5)
        self.start()

    # ── Convenience ────────────────────────────────────────────────────────

    def ensure_running(self) -> None:
        """确保 daemon 正在运行，未运行则启动"""
        if not self.is_running():
            self.start()

    def __del__(self):
        # 不在此清理 daemon — daemon 作为独立进程运行
        # 关闭 daemon 应通过显式调用 stop() 完成（如 ws_server 关闭时）
        # 若此处调用 stop()，在 CLI 脚本退出时会导致 daemon 被终止，浏览器窗口关闭
        pass


# 全局单例
_manager = BrowserManager()


def get_manager() -> BrowserManager:
    return _manager
