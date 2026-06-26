"""通用文件级锁 — 确保对同一文件的操作不会并发（读-改-写安全）

提供四种锁能力：
1. 基础互斥锁（向后兼容原 acquire_file_lock）
2. _RWLock 读写锁（读共享、写排他）
3. LockManager 单例统一管理
4. 带超时的锁获取
"""

import asyncio
import contextlib
from pathlib import Path

# ── 向后兼容全局变量 ──────────────────────────────────────────────
# 按文件路径索引的锁池
_file_locks: dict[str, asyncio.Lock] = {}
_file_lock_lock = asyncio.Lock()  # 保护 _file_locks 本身的并发访问


async def acquire_file_lock(file_path: str | Path) -> asyncio.Lock:
    """获取指定文件的锁（按路径索引，自动创建）

    保持向后兼容，返回 asyncio.Lock 对象供 async with 使用。
    """
    manager = await LockManager.get_instance()
    key = str(Path(file_path).resolve())
    return await manager._get_or_create_lock(key)


# ── _RWLock: asyncio 读写锁 ──────────────────────────────────────

class _RWLock:
    """asyncio 读写锁 — 读共享、写排他

    使用 asyncio.Condition 实现，支持写优先以避免写者饥饿：
    - 多个读锁可同时持有
    - 写锁需要等待所有读锁释放
    - 写锁等待期间，新读锁也会等待（写优先）
    """

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._readers: int = 0          # 当前持有读锁的协程数
        self._writer: bool = False       # 是否有写锁持有
        self._pending_writers: int = 0   # 等待中的写锁数（防饥饿）

    async def acquire_read(self) -> None:
        """获取读锁 — 有写锁或等待写锁时阻塞"""
        async with self._cond:
            while self._writer or self._pending_writers > 0:
                await self._cond.wait()
            self._readers += 1

    async def release_read(self) -> None:
        """释放读锁 — 读锁归零时唤醒等待者"""
        async with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    async def acquire_write(self) -> None:
        """获取写锁 — 等待所有读锁释放且无其他写锁"""
        async with self._cond:
            self._pending_writers += 1
            try:
                while self._readers > 0 or self._writer:
                    await self._cond.wait()
                self._writer = True
            finally:
                self._pending_writers -= 1

    async def release_write(self) -> None:
        """释放写锁 — 唤醒所有等待者"""
        async with self._cond:
            self._writer = False
            self._cond.notify_all()

    @contextlib.asynccontextmanager
    async def read_lock(self):
        """读锁异步上下文管理器"""
        await self.acquire_read()
        try:
            yield
        finally:
            await self.release_read()

    @contextlib.asynccontextmanager
    async def write_lock(self):
        """写锁异步上下文管理器"""
        await self.acquire_write()
        try:
            yield
        finally:
            await self.release_write()


# ── LockManager: 单例锁管理器 ────────────────────────────────────

class LockManager:
    """文件锁管理器（单例）

    统一管理系统中所有文件锁的创建和获取，支持：
    - 互斥锁（向后兼容）
    - 读写锁
    - 多文件排序获取（死锁预防）
    - 调试统计
    """

    _instance: "LockManager | None" = None
    _singleton_lock = asyncio.Lock()

    def __new__(cls) -> "LockManager":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._file_locks: dict[str, asyncio.Lock] = {}
            instance._rw_locks: dict[str, _RWLock] = {}
            instance._lock = asyncio.Lock()  # 保护内部字典
            cls._instance = instance
        return cls._instance

    @classmethod
    async def get_instance(cls) -> "LockManager":
        """获取 LockManager 单例实例"""
        async with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # 内部辅助 ────────────────────────────────────────────────

    async def _get_or_create_lock(self, key: str) -> asyncio.Lock:
        async with self._lock:
            if key not in self._file_locks:
                self._file_locks[key] = asyncio.Lock()
            return self._file_locks[key]

    async def _get_or_create_rwlock(self, key: str) -> _RWLock:
        async with self._lock:
            if key not in self._rw_locks:
                self._rw_locks[key] = _RWLock()
            return self._rw_locks[key]

    # 公开接口 ────────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def acquire(self, file_path: str | Path):
        """获取文件互斥锁（向后兼容）

        使用 async with 语法：
            async with manager.acquire("path"):
                ...
        """
        key = str(Path(file_path).resolve())
        lock = await self._get_or_create_lock(key)
        async with lock:
            yield

    @contextlib.asynccontextmanager
    async def acquire_read(self, file_path: str | Path):
        """获取文件读锁（读共享）

        多个读锁可同时持有；有写锁等待时不颁发新读锁。
        使用 async with 语法：
            async with manager.acquire_read("path"):
                ...
        """
        key = str(Path(file_path).resolve())
        rwlock = await self._get_or_create_rwlock(key)
        async with rwlock.read_lock():
            yield

    @contextlib.asynccontextmanager
    async def acquire_write(self, file_path: str | Path):
        """获取文件写锁（写排他）

        写锁需要等待所有读锁释放；写锁等待期间新读锁也等待。
        使用 async with 语法：
            async with manager.acquire_write("path"):
                ...
        """
        key = str(Path(file_path).resolve())
        rwlock = await self._get_or_create_rwlock(key)
        async with rwlock.write_lock():
            yield

    @contextlib.asynccontextmanager
    async def acquire_multi(
        self,
        file_paths: list[str | Path],
        timeout: float = 10.0,
    ):
        """按字典序获取多个文件锁，防止死锁

        对所有路径排序后逐个获取；中途超时时释放已获取的锁。
        使用 async with 语法：
            async with manager.acquire_multi(["a.txt", "b.txt"], timeout=5.0) as locks:
                ...
        """
        keys = sorted(str(Path(p).resolve()) for p in file_paths)
        acquired: list[asyncio.Lock] = []
        try:
            for key in keys:
                lock = await self._get_or_create_lock(key)
                await asyncio.wait_for(lock.acquire(), timeout=timeout)
                acquired.append(lock)
            yield acquired
        except asyncio.TimeoutError:
            for lock in acquired:
                lock.release()
            raise
        finally:
            # 兜底释放 — 防止 yield 内抛出异常导致锁泄露
            for lock in acquired:
                if lock.locked():
                    lock.release()

    def get_stats(self) -> dict:
        """返回锁持有状态的调试信息"""
        return {
            "file_locks": {
                k: {"locked": v.locked()}
                for k, v in self._file_locks.items()
            },
            "rw_locks": {
                k: {
                    "readers": v._readers,
                    "writer": v._writer,
                    "pending_writers": v._pending_writers,
                }
                for k, v in self._rw_locks.items()
            },
        }


# ── 带超时的锁获取（便捷函数） ───────────────────────────────────

async def acquire_file_lock_with_timeout(
    file_path: str | Path,
    timeout: float = 5.0,
) -> asyncio.Lock | None:
    """获取文件锁，超时返回 None 而非永久阻塞

    返回已持有的锁对象，调用方必须显式释放：
        lock = await acquire_file_lock_with_timeout("path", timeout=3.0)
        if lock is not None:
            try:
                ...
            finally:
                lock.release()

    Args:
        file_path: 文件路径
        timeout:   超时秒数（默认 5.0）

    Returns:
        已持有的 asyncio.Lock，超时返回 None
    """
    manager = await LockManager.get_instance()
    key = str(Path(file_path).resolve())
    lock = await manager._get_or_create_lock(key)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout)
        return lock
    except asyncio.TimeoutError:
        return None
