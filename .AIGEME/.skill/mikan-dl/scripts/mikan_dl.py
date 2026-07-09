#!/usr/bin/env python3
"""mikan_dl — 轻量级 BT/磁力链/流媒体下载器

用法:
    python mikan_dl.py add <url_or_file> [-o DIR] [-n NAME]  # 添加下载（立即返回）
    python mikan_dl.py list                                    # 查看所有任务
    python mikan_dl.py status <task_id>                        # 查看任务详情
    python mikan_dl.py pause <task_id>                         # 暂停
    python mikan_dl.py resume <task_id>                        # 恢复
    python mikan_dl.py remove <task_id> [--delete-files]       # 删除任务
    python mikan_dl.py daemon                                  # 前台运行 daemon（调试用）
"""

import argparse
import hashlib
import json
import os
import signal
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 配置 ──────────────────────────────────────────────
APP_NAME = "mikan_dl"
DEFAULT_PORT = 6881
DEFAULT_UPLOAD_LIMIT = 0  # 不限速
UPDATE_INTERVAL = 2  # 状态刷新间隔(秒)
STATE_FILE = "downloads.json"
LOCK_FILE = ".mikan_dl.lock"
PID_FILE = ".mikan_dl.pid"

# ── 路径 ──────────────────────────────────────────────
APP_DIR = Path(__file__).resolve().parent
STATE_PATH = APP_DIR / STATE_FILE
LOCK_PATH = APP_DIR / LOCK_FILE
PID_PATH = APP_DIR / PID_FILE
DEFAULT_OUTPUT = str(Path.home() / "Downloads")


# ════════════════════════════════════════════════════════
#  StateStore — JSON 持久化
# ════════════════════════════════════════════════════════
class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"tasks": {}}
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data: Dict[str, Any]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _modify(self, fn) -> Any:
        with self._lock:
            data = self._load()
            result = fn(data)
            self._save(data)
            return result

    def create_task(self, task_id: str, task_info: Dict[str, Any]) -> None:
        def _create(data):
            data["tasks"][task_id] = task_info
        self._modify(_create)

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> None:
        def _update(data):
            if task_id in data["tasks"]:
                data["tasks"][task_id].update(updates)
                data["tasks"][task_id]["updated_at"] = now_str()
        self._modify(_update)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        return data["tasks"].get(task_id)

    def get_all_tasks(self) -> Dict[str, Dict[str, Any]]:
        data = self._load()
        return data["tasks"]

    def remove_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        def _remove(data):
            return data["tasks"].pop(task_id, None)
        return self._modify(_remove)

    def get_active_tasks(self) -> List[Dict[str, Any]]:
        data = self._load()
        return [t for t in data["tasks"].values() if t["status"] == "downloading"]


# ════════════════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════════════════
def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def gen_task_id(source: str) -> str:
    ts = int(time.time() * 1000) & 0xFFFFFFFF
    h = hashlib.md5(f"{source}{ts}".encode()).hexdigest()[:8]
    return h


def fmt_size(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}PB"


def fmt_speed(bps: int) -> str:
    return fmt_size(bps) + "/s"


def fmt_eta(secs: int) -> str:
    if secs < 0:
        return "∞"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60}s"
    h, rem = divmod(secs, 3600)
    return f"{h}h{rem // 60}m"


def detect_type(source: str) -> str:
    """判断下载类型: torrent / magnet / stream"""
    # 路径处理：去掉首尾引号，规范化路径
    source_clean = source.strip().strip('"').strip("'")
    source_path = Path(source_clean)
    if source_path.suffix.lower() == ".torrent" and source_path.exists():
        return "torrent"
    if source.startswith("magnet:"):
        return "magnet"
    return "stream"


def is_daemon_running() -> bool:
    """检查 daemon 是否在运行"""
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text().strip())
        # Windows: 检查进程是否存在
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


# ════════════════════════════════════════════════════════
#  TorrentEngine — libtorrent 封装
# ════════════════════════════════════════════════════════
class TorrentEngine:
    def __init__(self):
        self.lt = __import__("libtorrent")
        self.session = self.lt.session()
        self._setup_session()
        self._handle_map: Dict[str, Any] = {}  # task_id → torrent_handle

    def _setup_session(self):
        s = self.session
        # libtorrent 2.0 API: 使用 session 直接方法配置
        # 端口监听
        s.listen_on(DEFAULT_PORT, DEFAULT_PORT + 10)

        # DHT 配置 - 添加更多节点提高连接速度
        dht_nodes = [
            ("router.bittorrent.com", 6881),
            ("dht.transmissionbt.com", 6881),
            ("router.utorrent.com", 6881),
            ("dht.libtorrent.org", 25401),
            ("dht.aelitis.com", 6881),
            ("dht.videolan.org", 6881),
            ("tracker.bittorrent.org", 6881),
            ("tracker.openbittorrent.com", 6881),
            ("open.demonii.com", 1337),
            ("explodie.org", 6969),
            ("tracker.opentrackr.org", 1337),
            ("tracker.torrent.eu.org", 451),
            ("tracker.tiny-vps.com", 6969),
            ("tracker.pirateparty.gr", 6969),
            (" tracker.dler.org", 6969),
        ]
        for node, port in dht_nodes:
            try:
                s.add_dht_router(node, port)
            except Exception:
                pass  # 忽略添加失败的节点
        s.start_dht()

        # UPnP / NAT-PMP
        s.start_upnp()
        s.start_natpmp()

        # 磁盘缓存配置（千兆网需要大缓存跟上速度）
        # 使用 set_download_rate_limit 和 set_upload_rate_limit 控制速度
        # 缓存通过 settings 配置
        try:
            settings = s.get_settings()
            # 尝试设置缓存大小（如果支持）
            if hasattr(settings, 'set_int'):
                settings.set_int(self.lt.settings_pack.cache_size, 16384)
                settings.set_int(self.lt.settings_pack.cache_expiry, 60)
                s.apply_settings(settings)
        except Exception:
            # 如果 settings API 不可用，使用默认缓存
            pass

        # 限速
        if DEFAULT_UPLOAD_LIMIT > 0:
            s.set_upload_rate_limit(DEFAULT_UPLOAD_LIMIT)

    def add_torrent(self, task_id: str, torrent_path: str, output_dir: str) -> bool:
        try:
            # 读取种子文件内容
            with open(torrent_path, 'rb') as f:
                torrent_data = f.read()

            # 解析种子信息
            info = self.lt.torrent_info(torrent_data)

            # 准备添加参数
            params = {
                "save_path": output_dir,
                "storage_mode": self.lt.storage_mode_t.storage_mode_sparse,
                "ti": info,  # 传递 torrent_info 对象
            }

            # 添加种子
            handle = self.session.add_torrent(params)

            # 添加额外的 tracker 节点（如果种子自带的 tracker 不够）
            trackers = [
                "udp://tracker.opentrackr.org:1337/announce",
                "udp://open.demonii.com:1337/announce",
                "udp://explodie.org:6969/announce",
                "udp://tracker.torrent.eu.org:451/announce",
                "udp://tracker.tiny-vps.com:6969/announce",
                "udp://tracker.pirateparty.gr:6969/announce",
                "udp://tracker.dler.org:6969/announce",
                "http://tracker.opentrackr.org:1337/announce",
            ]
            for tracker_url in trackers:
                try:
                    handle.add_tracker({"url": tracker_url})
                except Exception:
                    pass  # 忽略添加失败的 tracker

            self._handle_map[task_id] = handle
            return True
        except Exception as e:
            print(f"[ERROR] 添加种子失败: {e}")
            return False

    def add_magnet(self, task_id: str, magnet_url: str, output_dir: str) -> bool:
        try:
            params = {
                "save_path": output_dir,
                "storage_mode": self.lt.storage_mode_t.storage_mode_sparse,
            }
            handle = self.lt.add_magnet_uri(self.session, magnet_url, params)
            self._handle_map[task_id] = handle
            return True
        except Exception as e:
            print(f"[ERROR] 添加磁力链失败: {e}")
            return False

    def get_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        handle = self._handle_map.get(task_id)
        if not handle or not handle.is_valid():
            return None
        status = handle.status()

        # 计算ETA：如果下载速度>0且有剩余数据，计算剩余时间
        eta = -1
        if status.download_rate > 0 and status.total > status.total_done:
            remaining = status.total - status.total_done
            eta = int(remaining / status.download_rate)

        return {
            "progress": round(status.progress * 100, 1),
            "download_speed": status.download_rate,
            "upload_speed": status.upload_rate,
            "peers": status.num_peers,
            "total_size": status.total,
            "downloaded": status.total_done,
            "eta": eta,
            "name": handle.name() if handle.has_metadata() else "(获取元数据中...)",
        }

    def pause(self, task_id: str) -> bool:
        handle = self._handle_map.get(task_id)
        if handle and handle.is_valid():
            handle.pause()
            return True
        return False

    def resume(self, task_id: str) -> bool:
        handle = self._handle_map.get(task_id)
        if handle and handle.is_valid():
            handle.resume()
            return True
        return False

    def remove(self, task_id: str, delete_files: bool = False) -> bool:
        handle = self._handle_map.pop(task_id, None)
        if handle and handle.is_valid():
            self.session.remove_torrent(handle, 1 if delete_files else 0)
            return True
        return False

    def restore_from_state(self, task_id: str, state: Dict[str, Any]) -> bool:
        """从状态文件恢复任务（断点续传）"""
        source = state.get("source", "").strip().strip('"').strip("'")
        output_dir = state.get("output_dir", DEFAULT_OUTPUT).strip().strip('"').strip("'")
        if state.get("type") == "torrent" and Path(source).exists():
            return self.add_torrent(task_id, source, output_dir)
        elif state.get("type") == "magnet" and source.startswith("magnet:"):
            return self.add_magnet(task_id, source, output_dir)
        return False

    def save_session(self) -> bool:
        """保存session状态到文件，支持断点续传"""
        try:
            state = self.session.save_state()
            session_file = APP_DIR / ".mikan_dl_session"
            with open(session_file, 'wb') as f:
                f.write(state)
            return True
        except Exception as e:
            print(f"[WARN] 保存session失败: {e}")
            return False

    def load_session(self) -> bool:
        """从文件恢复session状态"""
        session_file = APP_DIR / ".mikan_dl_session"
        if not session_file.exists():
            return False
        try:
            with open(session_file, 'rb') as f:
                state = f.read()
            self.session.load_state(state)
            return True
        except Exception as e:
            print(f"[WARN] 恢复session失败: {e}")
            return False

    def wait_for_metadata(self, task_id: str, timeout: int = 30) -> Optional[str]:
        """等待磁力链元数据下载完成，返回文件名"""
        handle = self._handle_map.get(task_id)
        if not handle:
            return None
        start = time.time()
        while time.time() - start < timeout:
            if handle.has_metadata():
                return handle.name()
            time.sleep(0.5)
        return None


# ════════════════════════════════════════════════════════
#  StreamEngine — yt-dlp 封装
# ════════════════════════════════════════════════════════
class StreamEngine:
    def __init__(self):
        self.ydl = __import__("yt_dlp")
        self._process_map: Dict[str, Any] = {}  # 保留接口

    def start_download(self, task_id: str, url: str, output_dir: str) -> bool:
        """启动 yt-dlp 下载，通过 hook 更新状态"""
        store = StateStore(STATE_PATH)

        def progress_hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                speed = d.get("speed") or 0
                eta = d.get("eta") or -1
                pct = round(downloaded / total * 100, 1) if total > 0 else 0
                store.update_task(task_id, {
                    "progress": pct,
                    "download_speed": speed,
                    "downloaded": downloaded,
                    "total_size": total,
                    "eta": int(eta),
                    "status": "downloading",
                })
            elif d["status"] == "finished":
                store.update_task(task_id, {
                    "progress": 100.0,
                    "status": "done",
                    "downloaded": d.get("total_bytes", 0),
                })

        def postprocessor_hook(d):
            if d["status"] == "finished":
                filename = d.get("filename", "")
                if filename:
                    store.update_task(task_id, {"name": Path(filename).name})

        ydl_opts = {
            "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            # 流式下载：用 ffmpeg 作为外部下载器，支持断点续传
            "downloader": "ffmpeg",
            "downloader_args": {
                "ffmpeg": ["-reconnect", "1", "-reconnect_streamed", "1",
                           "-reconnect_delay_max", "5"],
            },
        }

        def _run():
            try:
                with self.ydl.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    title = info.get("title", "")
                    store.update_task(task_id, {
                        "name": title,
                        "progress": 100.0,
                        "status": "done",
                    })
            except Exception as e:
                store.update_task(task_id, {
                    "status": "error",
                    "error": str(e),
                })

        t = threading.Thread(target=_run, daemon=True, name=f"yt-{task_id}")
        t.start()
        return True


# ════════════════════════════════════════════════════════
#  TaskManager — 统一调度
# ════════════════════════════════════════════════════════
class TaskManager:
    def __init__(self):
        self.store = StateStore(STATE_PATH)
        self.torrent_engine: Optional[TorrentEngine] = None
        self.stream_engine: Optional[StreamEngine] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False

    def _ensure_torrent_engine(self):
        if not self.torrent_engine:
            self.torrent_engine = TorrentEngine()
        return self.torrent_engine

    def _ensure_stream_engine(self):
        if not self.stream_engine:
            self.stream_engine = StreamEngine()
        return self.stream_engine

    def add_task(self, source: str, output_dir: str, name: Optional[str] = None) -> str:
        """添加下载任务，立即返回 task_id"""
        # 清理路径：去掉首尾引号
        source_clean = source.strip().strip('"').strip("'")
        output_clean = output_dir.strip().strip('"').strip("'")

        # 确保输出目录存在
        output_path = Path(output_clean)
        output_path.mkdir(parents=True, exist_ok=True)

        task_id = gen_task_id(source_clean)
        dl_type = detect_type(source_clean)

        # 先创建状态
        task_info = {
            "id": task_id,
            "type": dl_type,
            "source": source_clean,
            "name": name or source_clean[:80],
            "output_dir": output_clean,
            "status": "downloading",
            "progress": 0.0,
            "download_speed": 0,
            "upload_speed": 0,
            "peers": 0,
            "total_size": 0,
            "downloaded": 0,
            "eta": -1,
            "created_at": now_str(),
            "updated_at": now_str(),
            "error": None,
        }
        self.store.create_task(task_id, task_info)

        # 启动下载（后台线程）
        success = False
        if dl_type in ("torrent", "magnet"):
            engine = self._ensure_torrent_engine()
            if dl_type == "torrent":
                success = engine.add_torrent(task_id, source_clean, output_clean)
            else:
                success = engine.add_magnet(task_id, source_clean, output_clean)
        else:
            engine = self._ensure_stream_engine()
            success = engine.start_download(task_id, source_clean, output_clean)

        if not success:
            self.store.update_task(task_id, {"status": "error", "error": "启动下载失败"})

        return task_id

    def pause_task(self, task_id: str) -> bool:
        if self.torrent_engine and self.torrent_engine.pause(task_id):
            self.store.update_task(task_id, {"status": "paused"})
            return True
        return False

    def resume_task(self, task_id: str) -> bool:
        if self.torrent_engine and self.torrent_engine.resume(task_id):
            self.store.update_task(task_id, {"status": "downloading"})
            return True
        return False

    def remove_task(self, task_id: str, delete_files: bool = False) -> bool:
        removed = False
        if self.torrent_engine:
            removed = self.torrent_engine.remove(task_id, delete_files)
        self.store.remove_task(task_id)
        return removed

    def start_monitor(self):
        """启动后台监控线程，持续更新任务状态"""
        self._running = True

        def _monitor():
            while self._running:
                tasks = self.store.get_all_tasks()
                for tid, task in tasks.items():
                    if task["status"] == "downloading" and task["type"] in ("torrent", "magnet"):
                        if self.torrent_engine:
                            st = self.torrent_engine.get_status(tid)
                            if st:
                                self.store.update_task(tid, st)
                                # 检查是否下载完成
                                if st["progress"] >= 100.0:
                                    self.store.update_task(tid, {"status": "done"})
                time.sleep(UPDATE_INTERVAL)

        self._monitor_thread = threading.Thread(target=_monitor, daemon=True, name="monitor")
        self._monitor_thread.start()

    def stop_monitor(self):
        self._running = False

    def restore_all(self) -> int:
        """从状态文件恢复所有未完成任务"""
        count = 0
        tasks = self.store.get_all_tasks()
        for tid, task in tasks.items():
            if task["status"] in ("downloading", "paused"):
                if task["type"] in ("torrent", "magnet"):
                    engine = self._ensure_torrent_engine()
                    if engine.restore_from_state(tid, task):
                        count += 1
                elif task["type"] == "stream":
                    engine = self._ensure_stream_engine()
                    if engine.start_download(tid, task["source"], task["output_dir"]):
                        count += 1
        return count


# ════════════════════════════════════════════════════════
#  CLI 命令
# ════════════════════════════════════════════════════════
def cmd_add(args):
    manager = TaskManager()
    source = args.source
    output_dir = args.output or DEFAULT_OUTPUT
    task_id = manager.add_task(source, output_dir, name=args.name)
    task = manager.store.get_task(task_id)
    print(f"\n  ✅ 任务已添加")
    print(f"  ID:    {task_id}")
    print(f"  类型:  {task['type']}")
    print(f"  保存:  {output_dir}")
    print(f"\n  查看进度: python mikan_dl.py status {task_id}\n")


def cmd_list(args):
    store = StateStore(STATE_PATH)
    tasks = store.get_all_tasks()

    if not tasks:
        print("\n  📭 没有下载任务\n")
        return

    # 表头
    print(f"\n  {'ID':<10} {'类型':<8} {'状态':<12} {'进度':>7} {'速度':>12} {'名称'}")
    print(f"  {'-'*10} {'-'*8} {'-'*12} {'-'*7} {'-'*12} {'-'*30}")

    for tid, t in sorted(tasks.items(), key=lambda x: x[1].get("created_at", ""), reverse=True):
        status_icon = {
            "downloading": "⬇️",
            "paused": "⏸️",
            "done": "✅",
            "error": "❌",
        }.get(t["status"], "❓")

        progress = f"{t['progress']:.1f}%"
        speed = fmt_speed(t["download_speed"]) if t["status"] == "downloading" else "-"
        name = (t.get("name") or t["source"])[:35]

        print(f"  {tid:<10} {t['type']:<8} {status_icon} {t['status']:<8} {progress:>7} {speed:>12} {name}")

    print(f"\n  共 {len(tasks)} 个任务\n")


def cmd_status(args):
    store = StateStore(STATE_PATH)
    task = store.get_task(args.task_id)

    if not task:
        print(f"\n  ❌ 任务 {args.task_id} 不存在\n")
        return

    print(f"\n  ═══ 任务详情 ═══")
    print(f"  ID:       {task['id']}")
    print(f"  类型:     {task['type']}")
    print(f"  状态:     {task['status']}")
    print(f"  名称:     {task.get('name', '-')}")
    print(f"  来源:     {task['source'][:60]}")
    print(f"  保存到:   {task['output_dir']}")
    print(f"  进度:     {task['progress']:.1f}%")
    print(f"  下载速度: {fmt_speed(task['download_speed'])}")
    print(f"  上传速度: {fmt_speed(task['upload_speed'])}")
    print(f"  已下载:   {fmt_size(task['downloaded'])} / {fmt_size(task['total_size'])}")
    print(f"  预计剩余: {fmt_eta(task['eta'])}")
    if task["type"] in ("torrent", "magnet"):
        print(f"  Peer数:   {task['peers']}")
    if task.get("error"):
        print(f"  错误:     {task['error']}")
    print(f"  创建时间: {task['created_at']}")
    print(f"  ═══════════════\n")


def cmd_pause(args):
    manager = TaskManager()
    task = manager.store.get_task(args.task_id)
    if not task:
        print(f"\n  ❌ 任务 {args.task_id} 不存在\n")
        return
    if manager.pause_task(args.task_id):
        print(f"\n  ⏸️  任务 {args.task_id} 已暂停\n")
    else:
        print(f"\n  ⚠️  无法暂停（可能是流媒体任务）\n")


def cmd_resume(args):
    manager = TaskManager()
    task = manager.store.get_task(args.task_id)
    if not task:
        print(f"\n  ❌ 任务 {args.task_id} 不存在\n")
        return
    if manager.resume_task(args.task_id):
        print(f"\n  ▶️  任务 {args.task_id} 已恢复\n")
    else:
        print(f"\n  ⚠️  无法恢复（可能是流媒体任务）\n")


def cmd_remove(args):
    manager = TaskManager()
    task = manager.store.get_task(args.task_id)
    if not task:
        print(f"\n  ❌ 任务 {args.task_id} 不存在\n")
        return
    delete = getattr(args, "delete_files", False)
    manager.remove_task(args.task_id, delete_files=delete)
    msg = "任务和文件已删除" if delete else "任务已删除（文件保留）"
    print(f"\n  🗑️  {msg}\n")


def cmd_daemon(args):
    """前台 daemon 模式，用于调试"""
    manager = TaskManager()

    # 尝试恢复session状态
    if manager.torrent_engine:
        manager.torrent_engine.load_session()

    restored = manager.restore_all()
    if restored:
        print(f"  恢复了 {restored} 个未完成任务")
    manager.start_monitor()
    print("  🔄 Daemon 运行中... (Ctrl+C 退出)\n")

    def _signal_handler(sig, frame):
        manager.stop_monitor()
        # 退出前保存session状态
        if manager.torrent_engine:
            manager.torrent_engine.save_session()
            print("  Session状态已保存")
        print("\n  Daemon 已退出")
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 保持前台运行，每30秒保存一次session
    last_save = time.time()
    while True:
        time.sleep(1)
        # 每30秒自动保存session
        if time.time() - last_save > 30:
            if manager.torrent_engine:
                manager.torrent_engine.save_session()
            last_save = time.time()


# ════════════════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="轻量级 BT/磁力链/流媒体下载器",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # add
    p_add = sub.add_parser("add", help="添加下载任务")
    p_add.add_argument("source", type=str, help="种子文件路径 / 磁力链 / URL")
    p_add.add_argument("-o", "--output", type=str, default=None, help="保存目录")
    p_add.add_argument("-n", "--name", type=str, default=None, help="自定义任务名称")

    # list
    sub.add_parser("list", help="查看所有任务")

    # status
    p_status = sub.add_parser("status", help="查看任务详情")
    p_status.add_argument("task_id", type=str, help="任务 ID")

    # pause
    p_pause = sub.add_parser("pause", help="暂停任务")
    p_pause.add_argument("task_id", type=str, help="任务 ID")

    # resume
    p_resume = sub.add_parser("resume", help="恢复任务")
    p_resume.add_argument("task_id", type=str, help="任务 ID")

    # remove
    p_remove = sub.add_parser("remove", help="删除任务")
    p_remove.add_argument("task_id", type=str, help="任务 ID")
    p_remove.add_argument("--delete-files", action="store_true", help="同时删除已下载文件")

    # daemon
    sub.add_parser("daemon", help="前台运行 daemon（调试用）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    cmd_map = {
        "add": cmd_add,
        "list": cmd_list,
        "status": cmd_status,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "remove": cmd_remove,
        "daemon": cmd_daemon,
    }
    cmd_map[args.command](args)


if __name__ == "__main__":
    main()
