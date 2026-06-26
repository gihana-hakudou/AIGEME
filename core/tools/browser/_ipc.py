"""IPC plumbing between helpers (sync) and daemon (async CDP).

On Windows: TCP loopback with a random port + bearer token.
On POSIX:   AF_UNIX socket with mode 0600.
"""

import asyncio
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# Use project directory instead of system temp to avoid permission issues
# Check if user explicitly set BU_RUNTIME_DIR/BU_TMP_DIR, otherwise use project root
_BU_RUNTIME_DIR = os.environ.get("BU_RUNTIME_DIR")
_BU_TMP_DIR = os.environ.get("BU_TMP_DIR")

# Default to project's character_data/browser directory
if not _BU_RUNTIME_DIR and not _BU_TMP_DIR:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
    _DEFAULT_RUNTIME = _PROJECT_ROOT / "character_data" / "browser" / "runtime"
    _DEFAULT_TMP = _PROJECT_ROOT / "character_data" / "browser" / "tmp"
    _DEFAULT_RUNTIME.mkdir(parents=True, exist_ok=True)
    _DEFAULT_TMP.mkdir(parents=True, exist_ok=True)
    BU_RUNTIME_DIR = str(_DEFAULT_RUNTIME)
    BU_TMP_DIR = str(_DEFAULT_TMP)
else:
    BU_RUNTIME_DIR = _BU_RUNTIME_DIR
    BU_TMP_DIR = _BU_TMP_DIR

_TMP = Path(BU_TMP_DIR or tempfile.gettempdir()).expanduser()
_RUNTIME = Path(BU_RUNTIME_DIR or tempfile.gettempdir()).expanduser()
_TMP.mkdir(parents=True, exist_ok=True)
_RUNTIME.mkdir(parents=True, exist_ok=True)

_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")

_port_file_cache: dict[str, tuple[int, str] | None] = {}


def _check(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(
            f"invalid BU_NAME {name!r}: must match [A-Za-z0-9_-]{{1,64}}"
        )
    return name


def _runtime_stem(name: str) -> str:
    _check(name)
    return "bu" if BU_RUNTIME_DIR else f"bu-{name}"


def _tmp_stem(name: str) -> str:
    _check(name)
    return "bu" if BU_TMP_DIR else f"bu-{name}"


def sock_addr(name: str) -> str:
    if not IS_WINDOWS:
        return str(_RUNTIME / f"{_runtime_stem(name)}.sock")
    port, _ = _read_port_file(name)
    return f"127.0.0.1:{port}" if port else f"tcp:{_runtime_stem(name)}"


def log_path(name: str) -> Path:
    return _TMP / f"{_tmp_stem(name)}.log"


def pid_path(name: str) -> Path:
    return _RUNTIME / f"{_runtime_stem(name)}.pid"


def port_path(name: str) -> Path:
    return _RUNTIME / f"{_runtime_stem(name)}.port"


def _read_port_file(name: str, bust_cache: bool = False) -> tuple[int | None, str | None]:
    if not bust_cache and name in _port_file_cache:
        cached = _port_file_cache[name]
        # Only use cache if it has a valid port (don't cache None results)
        if cached[0] is not None:
            return cached
    p = port_path(name)
    try:
        d = json.loads(p.read_text())
        result = (int(d["port"]), str(d["token"]))
    except (FileNotFoundError, ValueError, KeyError, TypeError, OSError):
        result = (None, None)
    if result[0] is not None:
        _port_file_cache[name] = result
    else:
        # 文件不存在或无效时，清除旧缓存，避免用过期端口
        _port_file_cache.pop(name, None)
    return result


def invalidate_port_cache(name: str):
    """手动清除指定 name 的端口缓存，通常在 daemon 重启后调用。"""
    _port_file_cache.pop(name, None)


def spawn_kwargs():
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
                                | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def connect(name: str, timeout: float = 5.0) -> tuple[socket.socket, str | None]:
    if not IS_WINDOWS:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(_RUNTIME / f"{_runtime_stem(name)}.sock"))
        return s, None

    port, token = _read_port_file(name)
    if port is None:
        raise FileNotFoundError(
            f"Daemon port file not found: {port_path(name)} — is the daemon running?"
        )
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    except (ConnectionRefusedError, OSError):
        # Cache may be stale (daemon was restarted). Force bust cache and retry.
        port, token = _read_port_file(name, bust_cache=True)
        if port is None:
            raise
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.settimeout(timeout)
    return s, token


def request(sock: socket.socket, token: str | None, req: dict) -> dict:
    if token:
        req = {**req, "token": token}
    sock.sendall((json.dumps(req) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(1 << 16)
        if not chunk:
            break
        data += chunk
    return json.loads(data or b"{}")


def ping(name: str, timeout: float = 1.0) -> bool:
    try:
        s, token = connect(name, timeout=timeout)
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError,
            OSError, socket.timeout):
        return False
    try:
        resp = request(s, token, {"meta": "ping"})
        return isinstance(resp, dict) and resp.get("pong") is True
    except (OSError, ValueError, AttributeError):
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def identify(name: str, timeout: float = 1.0):
    try:
        c, token = connect(name, timeout=timeout)
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        return None
    try:
        resp = request(c, token, {"meta": "ping"})
        if not isinstance(resp, dict) or resp.get("pong") is not True:
            return None
        pid = resp.get("pid")
        if type(pid) is int and 0 < pid < (1 << 31):
            return pid
        return None
    except (OSError, ValueError, AttributeError):
        return None
    finally:
        try:
            c.close()
        except OSError:
            pass


async def serve(name: str, handler):
    if not IS_WINDOWS:
        path = str(_RUNTIME / f"{_runtime_stem(name)}.sock")
        if os.path.exists(path):
            os.unlink(path)
        old_umask = os.umask(0o077)
        try:
            server = await asyncio.start_unix_server(handler, path=path)
        finally:
            os.umask(old_umask)
        asyncio.create_task(_wait_for_stop(server))
        await asyncio.Event().wait()
    else:
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        token = secrets.token_hex(32)
        pf = port_path(name)
        tmp = pf.with_suffix(pf.suffix + ".tmp")
        tmp.write_text(json.dumps({"port": port, "token": token}))
        os.replace(tmp, pf)
        serve._server_token = token
        asyncio.create_task(_wait_for_stop(server))
        await asyncio.Event().wait()


async def _wait_for_stop(server):
    pass


def expected_token() -> str | None:
    if not IS_WINDOWS:
        return None
    _, token = _read_port_file(os.environ.get("BU_NAME", "default"))
    return token


def cleanup_endpoint(name: str):
    p = (_RUNTIME / f"{_runtime_stem(name)}.sock") if not IS_WINDOWS else port_path(name)
    try:
        p.unlink()
    except FileNotFoundError:
        pass
