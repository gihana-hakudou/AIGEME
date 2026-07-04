"""MCP 服务器配置管理工具 — McpAddServer / McpUpdateServer / McpDeleteServer / McpListServers

基于设计文档 mcp-server-config-design-2026-06-26.md 实现。
安全策略（SEC-01 ~ SEC-08）在 core/mcp_server/manager.py 中实现。
"""

import asyncio
import logging
from typing import Any, Callable

from core.tools.base import BaseTool
from core.mcp_server.manager import McpServerManager

logger = logging.getLogger(__name__)

# ── 配置管理器单例 ──────────────────────────────────


def _get_manager() -> McpServerManager:
    """获取 MCP 管理器单例（不缓存，每次走 get_instance()）
    
    避免模块热加载后 mcp_tools 的全局 _manager 指向旧实例。
    """
    return McpServerManager.get_instance()


# ── MCP 工具刷新回调 ──────────────────────────────────
# 由 create_app() 在启动时注入，供配置变更后重新连接 + 注册工具
_refresh_callback: Callable[[], Any] | None = None
# 持有 _trigger_refresh 创建的 Task 引用，防止 GC 回收
_refresh_task: asyncio.Task | None = None


def set_refresh_callback(callback: Callable[[], Any]) -> None:
    """设置 MCP 工具刷新回调（由 create_app 启动时调用）

    当用户添加/更新/删除 MCP 服务器后，工具会自动调用此回调
    以重新连接 MCP 服务器并刷新 ToolRegistry 中的工具列表。
    """
    global _refresh_callback
    _refresh_callback = callback


def _log_task_exception(task: asyncio.Task) -> None:
    """记录异步任务中未被捕获的异常"""
    try:
        exc = task.exception()
        if exc:
            logger.warning("[MCP] 刷新后台任务异常: %s", exc)
    except asyncio.CancelledError:
        pass  # 任务取消是正常的


def _trigger_refresh() -> None:
    """触发 MCP 工具刷新（在配置变更后异步调用）"""
    global _refresh_task
    if _refresh_callback is not None:
        try:
            _refresh_task = asyncio.create_task(_refresh_callback())
            _refresh_task.add_done_callback(_log_task_exception)
            logger.info("[MCP] 已触发工具刷新（异步）")
        except Exception as e:
            logger.error("[MCP] 触发工具刷新失败: %s", e)
    else:
        logger.warning("[MCP] refresh_callback 未设置，无法自动刷新 MCP 工具")


# ── McpAddServer ─────────────────────────────────────


class McpAddServerTool(BaseTool):
    """添加 MCP 服务器配置"""

    name = "mcp_add_server"
    description = (
        "添加一个新的 MCP（Model Context Protocol）服务器配置。"
        "添加后，AIGEME 可以在运行时连接到该服务器并使用其提供的工具。"
        "支持的传输协议：stdio（本地进程）、sse（SSE 流式）、streamable_http（可流式HTTP）。"
        "命令白名单限制：仅允许 npx、uvx、python3、node、python、dotnet、java。"
    )
    output_type = "json"

    parameters = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "服务器唯一标识（必填，创建后不可修改）",
            },
            "name": {
                "type": "string",
                "description": "服务器显示名称（必填）",
            },
            "description": {
                "type": "string",
                "description": "服务器描述（可选，用于说明这个服务器提供什么功能，不超过256字符）",
            },
            "transport": {
                "type": "string",
                "enum": ["stdio", "sse", "streamable_http"],
                "description": "传输协议（必填）: stdio=本地进程, sse=SSE流式, streamable_http=可流式HTTP",
            },
            "config": {
                "type": "object",
                "description": "传输配置（必填，取决于 transport）:\n"
                               "- stdio: {\"stdio\": {\"command\": \"npx\", \"args\": [...], \"env\": {}}}\n"
                               "- sse: {\"sse\": {\"url\": \"...\", \"headers\": {}}}\n"
                               "- streamable_http: {\"streamable_http\": {\"url\": \"...\", \"headers\": {}}}",
                "properties": {
                    "stdio": {
                        "type": "object",
                        "description": "stdio 传输配置",
                        "properties": {
                            "command": {"type": "string", "description": "启动命令（需在白名单中）"},
                            "args": {"type": "array", "items": {"type": "string"}, "description": "命令参数数组"},
                            "env": {"type": "object", "description": "环境变量"},
                        },
                    },
                    "sse": {
                        "type": "object",
                        "description": "SSE 传输配置",
                        "properties": {
                            "url": {"type": "string", "description": "SSE 端点 URL"},
                            "headers": {"type": "object", "description": "自定义 HTTP 头"},
                        },
                    },
                    "streamable_http": {
                        "type": "object",
                        "description": "Streamable HTTP 传输配置",
                        "properties": {
                            "url": {"type": "string", "description": "HTTP 端点 URL"},
                            "headers": {"type": "object", "description": "自定义 HTTP 头"},
                        },
                    },
                },
            },
            "enabled": {
                "type": "boolean",
                "description": "是否启用（默认 true）",
            },
        },
        "required": ["id", "name", "transport", "config"],
    }

    async def execute(self, **kwargs: Any) -> dict:  # type: ignore[override]
        server_id = kwargs.get("id", "").strip()
        if not server_id:
            return {"status": "error", "error": "id 不能为空"}

        name = kwargs.get("name", "").strip()
        if not name:
            return {"status": "error", "error": "name 不能为空"}

        params = {
            "name": name,
            "description": kwargs.get("description", ""),
            "transport": kwargs.get("transport", "stdio"),
            "config": kwargs.get("config", {}),
            "enabled": kwargs.get("enabled", True),
        }

        manager = _get_manager()
        result = manager.add_server(server_id, params)
        # 添加成功后自动触发 MCP 工具刷新
        if result.get("status") == "ok":
            _trigger_refresh()
        return result


# ── McpUpdateServer ──────────────────────────────────


class McpUpdateServerTool(BaseTool):
    """更新 MCP 服务器配置（patch 语义 — 只更新提供的字段）"""

    name = "mcp_update_server"
    description = (
        "修改已有的 MCP 服务器配置。只需传入要修改的字段，未提供的字段会保持不变。"
        "注意：修改名称或描述会立即生效；修改传输协议或配置需要重新连接服务器。"
    )
    output_type = "json"

    parameters = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "要更新的服务器 ID（必填）",
            },
            "name": {
                "type": "string",
                "description": "新的显示名称（可选，修改后立即生效）",
            },
            "description": {
                "type": "string",
                "description": "新的描述（可选，修改后立即生效，不超过256字符）",
            },
            "transport": {
                "type": "string",
                "enum": ["stdio", "sse", "streamable_http"],
                "description": "新的传输协议（可选，修改后需要重新连接服务器）",
            },
            "config": {
                "type": "object",
                "description": "新的传输配置（可选，修改后需要重新连接服务器）",
            },
            "enabled": {
                "type": "boolean",
                "description": "是否启用（可选）",
            },
        },
        "required": ["id"],
    }

    async def execute(self, **kwargs: Any) -> dict:  # type: ignore[override]
        server_id = kwargs.get("id", "").strip()
        if not server_id:
            return {"status": "error", "error": "id 不能为空"}

        params: dict = {}
        for field in ("name", "description", "transport", "config", "enabled"):
            if field in kwargs:
                params[field] = kwargs[field]

        if not params:
            return {"status": "error", "error": "没有提供要更新的字段"}

        manager = _get_manager()
        result = manager.update_server(server_id, params)
        # 更新成功后自动触发 MCP 工具刷新
        if result.get("status") == "ok":
            _trigger_refresh()
        return result


# ── McpDeleteServer ──────────────────────────────────


class McpDeleteServerTool(BaseTool):
    """删除 MCP 服务器配置（软删除，保留 30 天可恢复）"""

    name = "mcp_delete_server"
    description = (
        "删除一个 MCP 服务器配置。删除后服务器会被停用，30 天内可以恢复（系统会保留配置）。"
        "返回该服务器之前是否处于活动状态。"
    )
    output_type = "json"

    parameters = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "要删除的服务器 ID（必填）",
            },
        },
        "required": ["id"],
    }

    async def execute(self, **kwargs: Any) -> dict:  # type: ignore[override]
        server_id = kwargs.get("id", "").strip()
        if not server_id:
            return {"status": "error", "error": "id 不能为空"}

        manager = _get_manager()
        result = manager.delete_server(server_id)
        # 删除成功后自动触发 MCP 工具刷新
        if result.get("status") == "ok":
            _trigger_refresh()
        return result


# ── McpListServers ───────────────────────────────────


class McpListServersTool(BaseTool):
    """列出所有已配置的 MCP 服务器及其状态"""

    name = "mcp_list_servers"
    description = (
        "列出所有已注册的 MCP 服务器配置。可选的 enabledOnly 参数可以只返回已启用且未删除的服务器。"
        "返回信息包括：ID、名称、描述、传输协议、状态（healthy/unhealthy/unknown）、启用状态、创建/更新时间。"
    )
    output_type = "json"

    parameters = {
        "type": "object",
        "properties": {
            "enabledOnly": {
                "type": "boolean",
                "description": "是否只返回已启用的服务器（可选，默认 false）",
            },
        },
    }

    async def execute(self, **kwargs: Any) -> dict:  # type: ignore[override]
        enabled_only = kwargs.get("enabledOnly", False)

        manager = _get_manager()
        servers = manager.list_servers(enabled_only=enabled_only)

        return {
            "status": "ok",
            "servers": servers,
            "count": len(servers),
        }
