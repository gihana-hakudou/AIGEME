"""MCP 运行时客户端代理 — 基于 litellm MCPClient 的服务器连接管理

提供：
- 连接生命周期管理
- 工具发现（list_tools）
- 工具调用转发
- 连接健康检查与重连
"""

import asyncio
import logging
from typing import Any

from litellm.experimental_mcp_client.client import MCPClient as LiteLLMMCPClient
from litellm.types.mcp import MCPTransport, MCPTransportType

from core.mcp_server.manager import McpServerManager

logger = logging.getLogger(__name__)


def _map_transport(transport_str: str) -> MCPTransportType:
    """将配置中的传输协议字符串映射到 litellm 枚举"""
    MAPPING = {
        "stdio": MCPTransport.stdio,
        "sse": MCPTransport.sse,
        "streamable_http": MCPTransport.http,
    }
    return MAPPING.get(transport_str, MCPTransport.stdio)


class McpClientConnection:
    """到单个 MCP 服务器的连接"""

    def __init__(self, server_id: str, server_config: dict) -> None:
        self.server_id = server_id
        self.config = server_config
        self._client: LiteLLMMCPClient | None = None
        self._connected = False
        self._tools_cache: list[dict] | None = None

    async def connect(self) -> bool:
        """连接到 MCP 服务器"""
        try:
            transport_str = self.config.get("transport", "stdio")
            transport_type = _map_transport(transport_str)
            cfg = self.config.get("config", {})

            if transport_str == "stdio":
                stdio_cfg = cfg.get("stdio", {})
                self._client = LiteLLMMCPClient(
                    transport_type=transport_type,
                    stdio_config={
                        "command": stdio_cfg.get("command", ""),
                        "args": stdio_cfg.get("args", []),
                        "env": stdio_cfg.get("env", {}),
                    },
                )
            else:
                # SSE / streamable_http
                transport_cfg = cfg.get(transport_str, {})
                headers = transport_cfg.get("headers", {})
                url = transport_cfg.get("url", "")

                self._client = LiteLLMMCPClient(
                    server_url=url,
                    transport_type=transport_type,
                    extra_headers=headers,
                )

            self._connected = True
            logger.info("MCP 客户端已连接: id=%s, transport=%s", self.server_id, transport_str)
            return True

        except Exception as e:
            logger.error("MCP 客户端连接失败: id=%s, error=%s", self.server_id, e)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """断开连接"""
        self._client = None
        self._connected = False
        self._tools_cache = None
        logger.info("MCP 客户端已断开: id=%s", self.server_id)

    def _ensure_connected(self) -> None:
        """确保已连接（状态检查，不抛异常）"""
        if not self._connected or self._client is None:
            raise ConnectionError(f"MCP 服务器 '{self.server_id}' 未连接")

    async def list_tools(self) -> list[dict]:
        """从连接的 MCP 服务器获取工具列表（委托给 litellm MCPClient）

        Returns:
            MCPTool 的 dict 列表（JSON 序列化安全）
        """
        self._ensure_connected()
        try:
            tools = await self._client.list_tools()  # type: ignore[union-attr]
            # 将 MCPTool 对象转为标准 dict
            result = []
            for t in tools:
                result.append({
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": dict(t.inputSchema) if hasattr(t, "inputSchema") else {},
                })
            self._tools_cache = result
            return result
        except Exception as e:
            logger.error("获取 MCP 工具列表失败: id=%s, error=%s", self.server_id, e)
            return []

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用 MCP 工具（委托给 litellm MCPClient）

        Returns:
            MCPCallToolResult 的 content 列表
        """
        self._ensure_connected()
        from mcp.types import CallToolRequestParams as MCPCallToolRequestParams
        params = MCPCallToolRequestParams(name=tool_name, arguments=arguments)
        try:
            result = await self._client.call_tool(params)  # type: ignore[union-attr]
            # 规范化返回格式
            content_list = []
            for item in (result.content if hasattr(result, "content") else []):
                if hasattr(item, "model_dump"):
                    content_list.append(item.model_dump())
                elif isinstance(item, dict):
                    content_list.append(item)
                else:
                    content_list.append({"type": "text", "text": str(item)})
            return {"status": "ok", "content": content_list}
        except Exception as e:
            logger.error("调用 MCP 工具失败: server=%s, tool=%s, error=%s",
                         self.server_id, tool_name, e)
            return {"status": "error", "error": str(e)}

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def tools_cache(self) -> list[dict] | None:
        """获取缓存的工具列表"""
        return self._tools_cache

    @property
    def server_info(self) -> dict:
        return {
            "id": self.server_id,
            "name": self.config.get("name", self.server_id),
            "transport": self.config.get("transport", "stdio"),
            "connected": self._connected,
        }


class McpRuntimeClient:
    """MCP 运行时客户端 — 管理所有已配置服务器的连接"""

    def __init__(self) -> None:
        self._connections: dict[str, McpClientConnection] = {}
        self._manager = McpServerManager.get_instance()

    async def connect_all(self) -> list[dict]:
        """连接所有已启用且未删除的服务器"""
        servers = self._manager.list_servers(enabled_only=True)
        results = []
        for svr in servers:
            conn = McpClientConnection(svr["id"], svr)
            ok = await conn.connect()
            if ok:
                self._connections[svr["id"]] = conn
            results.append({
                "id": svr["id"],
                "name": svr["name"],
                "connected": ok,
                "transport": svr["transport"],
            })
        return results

    async def connect_one(self, server_id: str) -> dict:
        """连接单个服务器"""
        svr = self._manager.get_server(server_id)
        if svr is None:
            return {"status": "error", "error": f"服务器 '{server_id}' 不存在或已删除"}

        conn = McpClientConnection(server_id, svr)
        ok = await conn.connect()
        if ok:
            self._connections[server_id] = conn
        return {
            "status": "ok" if ok else "error",
            "id": server_id,
            "name": svr["name"],
            "connected": ok,
        }

    async def disconnect_one(self, server_id: str) -> dict:
        """断开单个服务器连接"""
        conn = self._connections.pop(server_id, None)
        if conn:
            await conn.disconnect()
            return {"status": "ok", "id": server_id}
        return {"status": "ok", "id": server_id, "note": "not connected"}

    async def disconnect_all(self) -> None:
        """断开所有连接"""
        for conn in self._connections.values():
            await conn.disconnect()
        self._connections.clear()

    async def list_all_tools(self) -> dict[str, list[dict]]:
        """从所有已连接服务器收集工具列表

        Returns:
            {server_id: [tool_dict, ...]}
        """
        result: dict[str, list[dict]] = {}
        for sid, conn in self._connections.items():
            if conn.is_connected:
                tools = await conn.list_tools()
                result[sid] = tools
        return result

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict) -> dict:
        """在指定服务器上调用工具"""
        conn = self._connections.get(server_id)
        if conn is None:
            return {"status": "error", "error": f"服务器 '{server_id}' 未连接"}
        return await conn.call_tool(tool_name, arguments)

    def get_connected_servers(self) -> list[dict]:
        """获取所有已连接服务器的状态"""
        return [conn.server_info for conn in self._connections.values()]

    def is_connected(self, server_id: str) -> bool:
        conn = self._connections.get(server_id)
        return conn is not None and conn.is_connected


# 全局单例
_runtime_client: McpRuntimeClient | None = None


def get_runtime_client() -> McpRuntimeClient:
    """获取全局 MCP 运行时客户端单例"""
    global _runtime_client  # noqa: PLW0603
    if _runtime_client is None:
        _runtime_client = McpRuntimeClient()
    return _runtime_client
