"""MCP 工具适配器 — 将 MCP 协议工具包装为 AIGEME BaseTool

核心设计：
1. 每个 MCP 工具对应一个 McpToolAdapter 实例
2. name 格式: ``mcp_{server_id}_{tool_name}``（_ 分隔，保证唯一）
3. execute() 委托给对应的 McpClientConnection.call_tool()
4. 由 McpRuntimeClient 统一管理生命周期
"""

import json
import logging
from typing import Any

from core.tools.base import BaseTool, ToolOutputType

logger = logging.getLogger(__name__)

# MCP 工具注册前缀（用于在 ToolRegistry 中标识来源）
MCP_TOOL_PREFIX = "mcp"


def _mangle_tool_name(server_id: str, tool_name: str) -> str:
    """生成唯一工具名：mcp_{server_id}_{tool_name}

    Args:
        server_id: MCP 服务器 ID（内部唯一标识）
        tool_name: MCP 工具原始名称

    Returns:
        形如 ``mcp_code_search_search_files`` 的工具名
    """
    # 确保 server_id 中不包含非法字符
    safe_server_id = server_id.replace("-", "_").replace(".", "_").replace(" ", "_")
    return f"{MCP_TOOL_PREFIX}_{safe_server_id}_{tool_name}"


def _parse_mangled_name(mangled: str) -> tuple[str, str] | None:
    """从 mangled 工具名中解析出 server_id 和原始 tool_name

    ``mcp_code_search_search_files`` → (``code_search``, ``search_files``)

    Returns:
        (server_id, tool_name) 或 None（非 MCP 工具）
    """
    parts = mangled.split("_", 2)
    if len(parts) < 3 or parts[0] != MCP_TOOL_PREFIX:
        return None
    return parts[1], parts[2]


class McpToolAdapter(BaseTool):
    """MCP 工具适配器 — 将远程 MCP 工具包装成本地 BaseTool"""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    output_type: ToolOutputType = "json"

    def __init__(
        self,
        server_id: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
    ) -> None:
        self.name = _mangle_tool_name(server_id, tool_name)
        self.description = tool_description or f"MCP 工具 '{tool_name}'（来自服务器 {server_id}）"
        # 标准化 inputSchema 为 parameters 格式
        self.parameters = self._normalize_schema(input_schema)
        self.output_type = "json"
        self._server_id = server_id
        self._tool_name = tool_name
        super().__init__()

    @staticmethod
    def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """标准化 MCP inputSchema 为 BaseTool.parameters 兼容格式"""
        if not schema:
            return {"type": "object", "properties": {}}

        normalized = dict(schema)

        # 确保 type 为 object
        if "type" not in normalized:
            normalized["type"] = "object"
        # 确保 properties 存在
        if "properties" not in normalized:
            normalized["properties"] = {}

        return normalized

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """调用远程 MCP 工具

        返回格式::
            {
                "status": "ok" | "error",
                "content": [...],  # 工具返回的 content 列表
            }
        """
        from core.mcp_server.client import get_runtime_client

        client = get_runtime_client()
        result = await client.call_tool(self._server_id, self._tool_name, kwargs)
        return result

    def get_mcp_info(self) -> dict[str, str]:
        """获取 MCP 来源信息（用于调试和 L1 注入）"""
        return {
            "server_id": self._server_id,
            "tool_name": self._tool_name,
            "mangled_name": self.name,
        }
