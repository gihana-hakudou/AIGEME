"""FastAPI 应用工厂 — CORS、静态文件、WebSocket 路由（精简版）"""

import json
import logging
import sys
from contextlib import asynccontextmanager
from functools import partial

# 强制配置项目日志级别（覆盖 uvicorn 的 dictConfig）
# 关键是给 core logger 加一个自己的 handler，不走 root logger 的 handler
_core_handler = logging.StreamHandler(sys.stdout)
_core_handler.setLevel(logging.INFO)
_core_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
))

_core_logger = logging.getLogger("core")
_core_logger.setLevel(logging.INFO)
_core_logger.addHandler(_core_handler)
_core_logger.propagate = False  # 不传给 root logger（避免被 uvicorn 的 handler 过滤）

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.mcp_server.manager import McpServerManager
from core.memory.reminder_tool import ReminderTool
from core.memory.tools import MemoryTool
from core.tools.bash_tools import BashTool
from core.tools.document_tools import DocumentTool
from core.tools.mcp_tools import (
    McpAddServerTool,
    McpDeleteServerTool,
    McpListServersTool,
    McpUpdateServerTool,
)
from core.tools.registry import init_registry
from core.tools.skill_tools import SkillTool
from core.tools.system_tools import SystemTool
from core.tools.web_fetch import WebFetchTool
from core.tools.web_search import WebSearchTool
from core.plan.tool import PlanAndExecuteTool
from core.ws_server import WSServer

from core.utils import PROJECT_ROOT, diag
from core.mcp_lifespan import _connect_mcp_servers, _disconnect_mcp_servers, _refresh_mcp_tools
from core.routes import (
    characters_router,
    conversations_router,
    models_router,
    settings_router,
    frontend_router,
)

# 向后兼容：保持 `from core.main import PROVIDER_DEFAULTS` 等旧导入路径有效
from core.utils import PROVIDER_DEFAULTS, _split_model  # noqa: F401

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用"""
    diag("create_app() 被调用")

    # 先定义 lifespan（注册工具后，变量通过闭包引用）
    _lifespan_registry: "ToolRegistry | None" = None

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # ── startup ──
        diag("lifespan: startup")
        if _lifespan_registry is not None:
            await _connect_mcp_servers(_lifespan_registry)
        yield
        # ── shutdown ──
        diag("lifespan: shutdown")
        await _disconnect_mcp_servers()

    app = FastAPI(
        title="AIGEME",
        description="Galgame 叙事界面 + AI Agentic Harness",
        version="0.1.0",
        lifespan=_lifespan,
    )

    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8765", "http://127.0.0.1:8765"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载静态文件服务
    frontend_dir = PROJECT_ROOT / "frontend" / "chat"
    if frontend_dir.exists():
        app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    # 挂载立绘资源
    tachi_dir = PROJECT_ROOT / "tachi-e"
    if tachi_dir.exists():
        app.mount("/tachi-e", StaticFiles(directory=str(tachi_dir)), name="tachie")

    # 注册打包工具（browser_execute 已迁移至 CLI，改为通过 bash 调用）
    from core.config.settings import get_config
    cfg = get_config()
    ws_cfg = cfg.get("web_search", {})
    web_search_tool = WebSearchTool(
        backend=ws_cfg.get("backend", "tavily"),
        api_key=ws_cfg.get("api_key", ""),
        api_keys=ws_cfg.get("api_keys", None),
        max_results=ws_cfg.get("max_results", 5),
        timeout=ws_cfg.get("timeout", 10),
    )
    registry = init_registry(
        DocumentTool(),
        MemoryTool(),
        ReminderTool(),
        SkillTool(),
        BashTool(),
        SystemTool(),
        web_search_tool,
        WebFetchTool(),
        PlanAndExecuteTool(),
        McpAddServerTool(),
        McpUpdateServerTool(),
        McpDeleteServerTool(),
        McpListServersTool(),
    )
    _lifespan_registry = registry  # 供 lifespan 闭包引用

    # 注入 MCP 工具刷新回调（配置变更后自动重连+刷新工具注册）
    from core.tools.mcp_tools import set_refresh_callback
    set_refresh_callback(partial(_refresh_mcp_tools, registry))

    # 初始化 MCP 配置管理器（首次加载）
    McpServerManager.get_instance()

    # 注册浏览器工具（直接工具调用，绕过 bash 权限确认）
    from core.tools.browser import register_all as register_browser_tools
    register_browser_tools(registry)

    # 读取 LLM 部署模式
    llm_cfg = cfg.get("llm", {})
    llm_mode = llm_cfg.get("mode", "remote")
    is_local = llm_mode == "local"
    is_multimodal = llm_cfg.get("multimodal", False)

    # 权限检查已全部移至 bash_tools.py 的 _check_command_risk（写路径保护 + 权限模式）
    # 旧的 PermissionChain / BlocklistFilter / PathScopeFilter / ZonePermissionFilter 已移除

    # 初始化 WebSocket 服务器
    ws_server = WSServer(
        project_root=PROJECT_ROOT,
        registry=registry,
        multimodal=is_multimodal,
    )
    app.state.ws_server = ws_server

    # ── WebSocket 端点（保持内联，因为需要直接引用 ws_server）──
    @app.websocket("/ws/{character_id}")
    async def websocket_endpoint(ws: WebSocket, character_id: str) -> None:
        """WebSocket 连接端点"""
        diag(f"websocket_endpoint CALLED, char_id={character_id}")
        try:
            await ws_server.handle_connection(ws, character_id)
        except Exception as e:
            diag(f"websocket_endpoint EXCEPTION: {e!s}")
            raise

    # ── 注册路由模块（通过 FastAPI APIRouter）──
    app.include_router(characters_router)
    app.include_router(conversations_router)
    app.include_router(models_router)
    app.include_router(settings_router)
    app.include_router(frontend_router)

    return app
