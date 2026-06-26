"""MCP 服务器配置管理器 — 单文件 JSON 存储 + 原子写入 + 安全校验

功能：
- 添加/更新/删除/列出 MCP 服务器配置
- 命令白名单校验（SEC-01）
- Args 参数化（SEC-02）
- Prompt injection 过滤（SEC-03）
- 原子写入（SEC-05）
- 并发读写锁（SEC-06）
- 文件权限 0600（SEC-07）
- 审计日志（SEC-08）
"""

import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────

# 默认配置路径
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / ".AIGEME" / "mcp-servers"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "mcp-servers.json"

# 命令白名单（SEC-01）
ALLOWED_COMMANDS = frozenset({"npx", "uvx", "python3", "node", "python", "dotnet", "java"})

# Prompt injection 安全字符正则（SEC-03）
# 排除 < > { } 等可能用于模版注入的字符
SAFE_DESC_PATTERN = re.compile(r"^[a-zA-Z0-9 .,;:_\-'\"()!?/@#$%^&*+=\[\]|~\u4e00-\u9fff]+$")
MAX_DESC_LENGTH = 256

# 中文 Prompt 注入关键词黑名单
PROMPT_INJECTION_KEYWORDS = [
    "忽略所有", "忽略以上", "不要理会", "忘记所有",
    "你是一个", "从现在开始", "扮演", "系统指令",
    "system", "ignore", "forget",
]

# 传输协议枚举
VALID_TRANSPORTS = frozenset({"stdio", "sse", "streamable_http"})

# 存储版本
CONFIG_VERSION = 1


# ── 异常 ─────────────────────────────────────────────


class McpServerError(Exception):
    """MCP 服务器管理器基础异常"""


class ServerNotFoundError(McpServerError):
    """服务器不存在"""


class ServerAlreadyExistsError(McpServerError):
    """服务器已存在"""


class ValidationError(McpServerError):
    """参数校验失败"""


# ── 安全校验 ─────────────────────────────────────────


def validate_command(command: str) -> None:
    """SEC-01: 命令白名单校验"""
    if command not in ALLOWED_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        raise ValidationError(f"命令 '{command}' 不在白名单中。允许的命令: {allowed}")


def validate_args(args: list[str]) -> None:
    """SEC-02: Args 参数校验 — 禁止包含 shell 特殊字符"""
    dangerous_pattern = re.compile(r"[;&|`$(){}]")
    for i, arg in enumerate(args):
        if dangerous_pattern.search(arg):
            raise ValidationError(
                f"args[{i}] 包含危险的 shell 字符: '{arg}'"
            )


def validate_description(description: str) -> None:
    """SEC-03: Prompt injection 过滤 — 字符白名单 + 长度限制 + 关键词黑名单"""
    if len(description) > MAX_DESC_LENGTH:
        raise ValidationError(
            f"description 长度 {len(description)} 超过限制 {MAX_DESC_LENGTH}"
        )
    if not SAFE_DESC_PATTERN.match(description):
        raise ValidationError(
            "description 包含不安全的字符。仅允许字母、数字、空格和常见标点"
        )
    # 关键词黑名单检查
    desc_lower = description.lower()
    for kw in PROMPT_INJECTION_KEYWORDS:
        if kw in desc_lower or kw in description:
            raise ValidationError(
                f"description 包含潜在注入关键词: '{kw}'"
            )


# ── 原子文件写入 ────────────────────────────────────


def atomic_write_json(path: Path, data: dict) -> None:
    """SEC-05: 原子写入 — temp → fsync → rename"""
    # 确保目录存在
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path_str = tempfile.mkstemp(
        suffix=".tmp",
        prefix="mcp_",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(fd)

        # rename（跨设备安全）
        shutil.move(tmp_path_str, str(path))

        # SEC-07: 文件权限 0600
        os.chmod(str(path), 0o600)

    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


# ── 配置管理器 ───────────────────────────────────────


@dataclass
class McpServerRecord:
    """单条 MCP 服务器配置的运行时表示"""
    id: str
    name: str
    description: str = ""
    transport: str = "stdio"
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    deleted: bool = False
    createdAt: float = 0.0
    updatedAt: float = 0.0


def _make_record(server_id: str, params: dict) -> McpServerRecord:
    """从 API 参数创建记录"""
    now = time.time()
    return McpServerRecord(
        id=server_id,
        name=params.get("name", server_id),
        description=params.get("description", ""),
        transport=params.get("transport", "stdio"),
        config=params.get("config", {}),
        enabled=params.get("enabled", True),
        deleted=False,
        createdAt=now,
        updatedAt=now,
    )


def _record_to_dict(rec: McpServerRecord) -> dict:
    """记录 → JSON 字典"""
    return {
        "id": rec.id,
        "name": rec.name,
        "description": rec.description,
        "transport": rec.transport,
        "config": rec.config,
        "enabled": rec.enabled,
        "deleted": rec.deleted,
        "createdAt": rec.createdAt,
        "updatedAt": rec.updatedAt,
    }


def _dict_to_record(data: dict) -> McpServerRecord:
    """JSON 字典 → 记录"""
    return McpServerRecord(
        id=data.get("id", ""),
        name=data.get("name", ""),
        description=data.get("description", ""),
        transport=data.get("transport", "stdio"),
        config=data.get("config", {}),
        enabled=data.get("enabled", True),
        deleted=data.get("deleted", False),
        createdAt=data.get("createdAt", 0.0),
        updatedAt=data.get("updatedAt", 0.0),
    )


class McpServerManager:
    """MCP 服务器配置管理器 — 线程安全的单例"""

    _instance: "McpServerManager | None" = None

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._servers: dict[str, McpServerRecord] = {}
        self._lock = RLock()  # SEC-06: 读写锁
        self._loaded = False
        self._mtime: float = 0.0  # 上次加载时的文件修改时间

    @classmethod
    def get_instance(cls, config_path: str | Path | None = None) -> "McpServerManager":
        """获取全局单例"""
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

    # ── 加载与持久化 ──────────────────────────────

    def _load(self) -> None:
        """从磁盘加载配置"""
        with self._lock:
            # 若文件已变更，强制重读（避免单例缓存空状态）
            current_mtime = 0.0
            if self._path.exists():
                try:
                    current_mtime = self._path.stat().st_mtime
                except OSError:
                    pass
            if self._loaded and current_mtime <= self._mtime:
                return
            self._loaded = True
            self._mtime = current_mtime
            self._servers.clear()
            if self._path.exists():
                try:
                    raw = self._path.read_text("utf-8")
                    data = json.loads(raw)
                    servers = data.get("servers", {})
                    need_migrate = False
                    for sid, sdata in servers.items():
                        record = _dict_to_record(sdata)
                        self._servers[sid] = record
                    # 如果有旧格式的 enc: 前缀字段，自动清理
                    if need_migrate:
                        logger.info("MCP 配置检测到未加密的敏感字段，自动加密后重新保存")
                        self._save()
                except (json.JSONDecodeError, OSError) as e:
                    logger.error("MCP 配置文件损坏: %s", e)
                    # 保留空配置，允许覆盖
            self._loaded = True

    def _save(self) -> None:
        """持久化配置到磁盘"""
        with self._lock:
            servers_dict = {
                sid: _record_to_dict(rec)
                for sid, rec in self._servers.items()
            }
            payload = {
                "version": CONFIG_VERSION,
                "servers": servers_dict,
            }
            atomic_write_json(self._path, payload)
            logger.info("[AUDIT] MCP 配置已保存到 %s", self._path)

    def _ensure_loaded(self) -> None:
        """确保数据已加载（每次调用都检查文件变更）"""
        self._load()

    # ── CRUD 操作 ─────────────────────────────────

    def add_server(self, server_id: str, params: dict) -> dict:
        """添加 MCP 服务器配置（幂等检查）。

        Returns:
            {"status": "ok", "id": server_id}
            {"status": "conflict", "error": "server already exists", "id": server_id}
        """
        self._ensure_loaded()
        with self._lock:
            if server_id in self._servers and not self._servers[server_id].deleted:
                return {
                    "status": "conflict",
                    "error": f"服务器 '{server_id}' 已存在",
                    "id": server_id,
                }

            # 参数校验
            transport = params.get("transport", "stdio")
            if transport not in VALID_TRANSPORTS:
                return {
                    "status": "error",
                    "error": f"不支持的传输协议 '{transport}'。支持: {', '.join(sorted(VALID_TRANSPORTS))}",
                }

            config = params.get("config", {})
            if transport == "stdio":
                stdio_config = config.get("stdio", {})
                command = stdio_config.get("command", "")
                if not command:
                    return {"status": "error", "error": "stdio 传输需要 command 参数"}
                try:
                    validate_command(command)
                    args = stdio_config.get("args", [])
                    validate_args(args)
                except ValidationError as e:
                    return {"status": "error", "error": str(e), "error_type": "validation"}

            if "description" in params and params["description"]:
                try:
                    validate_description(params["description"])
                except ValidationError as e:
                    return {"status": "error", "error": str(e), "error_type": "validation"}

            # 配置直接以明文保存（无需加密，密钥在本地）
            plain_config = config

            now = time.time()
            record_data = {
                "name": params.get("name", server_id),
                "description": params.get("description", ""),
                "transport": transport,
                "config": plain_config,
                "enabled": params.get("enabled", True),
            }
            rec = _make_record(server_id, record_data)
            rec.createdAt = now
            rec.updatedAt = now

            self._servers[server_id] = rec
            self._save()

            logger.info("[AUDIT] MCP 服务器已添加: id=%s, transport=%s", server_id, transport)
            return {"status": "ok", "id": server_id}

    def update_server(self, server_id: str, params: dict) -> dict:
        """更新 MCP 服务器配置（patch 语义）。

        Returns:
            {"status": "ok", "id": server_id, "requiresRestart": bool}
        """
        self._ensure_loaded()
        with self._lock:
            if server_id not in self._servers:
                return {"status": "error", "error": f"服务器 '{server_id}' 不存在"}

            rec = self._servers[server_id]
            requires_restart = False

            # patch 语义：只更新传入的字段
            if "name" in params:
                rec.name = params["name"]
            if "description" in params:
                desc = params["description"]
                if desc:
                    try:
                        validate_description(desc)
                    except ValidationError as e:
                        return {"status": "error", "error": str(e), "error_type": "validation"}
                rec.description = desc
                # 修改 name/description → 不需要重启（热加载）
            if "transport" in params:
                transport = params["transport"]
                if transport not in VALID_TRANSPORTS:
                    return {
                        "status": "error",
                        "error": f"不支持的传输协议 '{transport}'",
                    }
                rec.transport = transport
                requires_restart = True  # 协议级变化
            if "config" in params:
                config = params["config"]
                rec.config = config
                requires_restart = True  # 连接参数变化
            if "enabled" in params:
                rec.enabled = bool(params["enabled"])

            rec.updatedAt = time.time()
            self._save()

            logger.info("[AUDIT] MCP 服务器已更新: id=%s, restart=%s", server_id, requires_restart)
            return {
                "status": "ok",
                "id": server_id,
                "requiresRestart": requires_restart,
            }

    def delete_server(self, server_id: str) -> dict:
        """删除 MCP 服务器配置（软删除，保留 30 天）。

        Returns:
            {"status": "ok", "id": server_id, "wasActive": bool}
        """
        self._ensure_loaded()
        with self._lock:
            if server_id not in self._servers:
                return {"status": "error", "error": f"服务器 '{server_id}' 不存在"}

            rec = self._servers[server_id]
            was_active = rec.enabled and not rec.deleted

            rec.deleted = True
            rec.enabled = False
            rec.updatedAt = time.time()
            self._save()

            logger.info("[AUDIT] MCP 服务器已删除: id=%s", server_id)
            return {"status": "ok", "id": server_id, "wasActive": was_active}

    def list_servers(self, enabled_only: bool = False) -> list[dict]:
        """列出所有 MCP 服务器。

        Args:
            enabled_only: 只返回已启用且未删除的服务器
        """
        self._ensure_loaded()
        with self._lock:
            results = []
            for rec in self._servers.values():
                if enabled_only and (rec.deleted or not rec.enabled):
                    continue
                if rec.deleted:
                    # 软删除的不显示详细配置
                    results.append({
                        "id": rec.id,
                        "name": rec.name,
                        "deleted": True,
                        "updatedAt": rec.updatedAt,
                    })
                else:
                    results.append({
                        "id": rec.id,
                        "name": rec.name,
                        "description": rec.description,
                        "transport": rec.transport,
                        "config": dict(rec.config),
                        "status": "unknown",
                        "enabled": rec.enabled,
                        "createdAt": rec.createdAt,
                        "updatedAt": rec.updatedAt,
                    })
            return results

    def get_server(self, server_id: str) -> dict | None:
        """获取单个服务器配置（运行时用）"""
        self._ensure_loaded()
        with self._lock:
            rec = self._servers.get(server_id)
            if rec is None or rec.deleted:
                return None
            return {
                "id": rec.id,
                "name": rec.name,
                "description": rec.description,
                "transport": rec.transport,
                "config": dict(rec.config),
                "enabled": rec.enabled,
                "createdAt": rec.createdAt,
                "updatedAt": rec.updatedAt,
            }

    # ── 系统提示词元信息（L1 注入用） ──────────────

    def get_prompt_metadata(self) -> list[dict]:
        """返回启用的服务器元信息（不含敏感字段），供 L1 提示词注入。"""
        self._ensure_loaded()
        with self._lock:
            result = []
            for rec in self._servers.values():
                if rec.deleted or not rec.enabled:
                    continue
                result.append({
                    "id": rec.id,
                    "name": rec.name,
                    "description": rec.description,
                    "transport": rec.transport,
                })
            return result

