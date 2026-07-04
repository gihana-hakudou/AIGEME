"""MCP 服务器生命周期管理 — 启动连接、工具注册、断开清理"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 可变容器，用于在连接/断开/刷新间传递状态
_MCP_REGISTRY_REF: dict[str, Any] = {}


async def _connect_mcp_servers(registry: "ToolRegistry") -> None:
    """连接所有已启用的 MCP 服务器，并将暴露的工具注册到 ToolRegistry

    接线流程:
    1. 调用 runtime_client.connect_all()
    2. 对每个已连接服务器调用 list_tools()
    3. 为每个工具创建 McpToolAdapter 并注册到 registry
    """
    from core.mcp_server.client import get_runtime_client
    from core.mcp_server.tool_adapter import McpToolAdapter

    client = get_runtime_client()
    results = await client.connect_all()
    connected = [r for r in results if r.get("connected")]
    logger.info("[MCP] connect_all 完成: %d 已连接 / %d 总计",
                len(connected), len(results))

    # 收集工具并注册
    all_tools = await client.list_all_tools()
    registered_names: list[str] = []
    registered_count = 0
    for sid, tools in all_tools.items():
        for t in tools:
            try:
                adapter = McpToolAdapter(
                    server_id=sid,
                    tool_name=t["name"],
                    tool_description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                )
                registry.register(adapter)
                registered_names.append(adapter.name)
                registered_count += 1
            except Exception as e:
                logger.error("[MCP] 注册工具失败: server=%s, tool=%s, error=%s",
                             sid, t.get("name", "?"), e)
    logger.info("[MCP] 已注册 %d 个 MCP 工具到 ToolRegistry", registered_count)
    _MCP_REGISTRY_REF["registered_count"] = registered_count
    _MCP_REGISTRY_REF["tool_names"] = registered_names


async def _disconnect_mcp_servers() -> None:
    """断开所有 MCP 服务器连接（shutdown 时调用）"""
    from core.mcp_server.client import get_runtime_client

    client = get_runtime_client()
    await client.disconnect_all()
    logger.info("[MCP] 所有 MCP 服务器已断开")


async def _refresh_mcp_tools(registry: "ToolRegistry") -> None:
    """刷新 MCP 工具注册：先删除旧的 MCP 工具，再重新连接+注册

    供配置变更（add/update/delete server）后调用。
    """
    from core.mcp_server.client import get_runtime_client

    # 1. 断开所有现有连接（自动清空 McpClientConnection）
    client = get_runtime_client()
    await client.disconnect_all()
    logger.info("[MCP] _refresh_mcp_tools: 旧连接已断开")

    # 2. 从 ToolRegistry 中移除之前注册的 MCP 工具（只移除适配器，不移除管理工具）
    old_names = _MCP_REGISTRY_REF.get("tool_names", [])
    removed = 0
    for name in old_names:
        if name in registry.names:
            registry._tools.pop(name, None)  # type: ignore[attr-defined]
            removed += 1
    _MCP_REGISTRY_REF["tool_names"] = []
    logger.info("[MCP] _refresh_mcp_tools: 已移除 %d 个旧 MCP 工具", removed)

    # 3. 重新连接并注册
    await _connect_mcp_servers(registry)
