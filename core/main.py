"""FastAPI 应用工厂 — CORS、静态文件、WebSocket 路由"""

import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

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
from core.permission_mode import PermissionMode
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
from core.tools.web_search import WebSearchTool
from core.plan.tool import PlanAndExecuteTool
from core.ws_server import WSServer

logger = logging.getLogger(__name__)


def _split_model(raw: str) -> tuple[str, str]:
    """拆分 litellm 模型名 ``provider/model_name`` → (provider, model_name)

    litellm 用第一个 ``/`` 前的部分作为 provider 路由前缀。
    若无 ``/``，则 provider 为空、整体作为 model_name 返回。
    """
    if not raw:
        return "", ""
    if "/" in raw:
        provider, _, name = raw.partition("/")
        return provider.strip(), name.strip()
    return "", raw.strip()


def _set_user_env_var(name: str, value: str) -> None:
    """持久化用户环境变量（Windows 注册表），跨进程重启有效"""
    import subprocess, sys
    # 使用 setx 写入 HKCU\Environment
    if sys.platform == "win32":
        subprocess.run(
            ["setx", name, value],
            capture_output=True, text=True, timeout=10
        )


def _del_user_env_var(name: str) -> None:
    """从 Windows 注册表删除用户环境变量"""
    import subprocess, sys
    if sys.platform == "win32":
        subprocess.run(
            ["reg", "delete", "HKCU\\Environment", "/v", name, "/f"],
            capture_output=True, text=True, timeout=10
        )


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent


# ── 权限模式（存内存，通过 bash_tools 模块级变量）──

def _get_perm_mode() -> str:
    """获取当前权限模式，默认 'normal'"""
    try:
        from core.tools.bash_tools import get_permission_mode
        return get_permission_mode()
    except Exception:
        return "normal"


def _set_perm_mode(mode: str) -> None:
    """设置权限模式"""
    try:
        from core.tools.bash_tools import set_permission_mode
        set_permission_mode(mode)
    except Exception:
        pass

# === 诊断日志文件 ===
_DIAG_LOG = PROJECT_ROOT / "diag_ws.log"


def _diag(msg: str) -> None:
    """写诊断日志到文件（带立即刷新）"""
    try:
        with open(_DIAG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{__import__('datetime').datetime.now()}] [main] {msg}\n")
            f.flush()
    except Exception:
        pass


# Provider 默认配置（api_base + 是否走原生 litellm 路由）
PROVIDER_DEFAULTS: dict[str, dict] = {
    "openai": {"api_base": "https://api.openai.com", "native": True},
    "custom_openai": {"api_base": "", "native": False},
    "anthropic": {"api_base": "https://api.anthropic.com", "native": True},
    "deepseek": {"api_base": "https://api.deepseek.com", "native": True},
    "dashscope": {"api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "native": False},
    "bigmodel": {"api_base": "https://open.bigmodel.cn/api/paas/v4", "native": False},
    "azure": {"api_base": "https://YOUR_RESOURCE.openai.azure.com", "native": True},
    "gemini": {"api_base": "https://generativelanguage.googleapis.com", "native": True},
    # 本地部署 — 按默认端口分组
    "ollama": {"api_base": "http://localhost:11434", "native": False},
    "local_8080": {"api_base": "http://localhost:8080", "native": False},
    "local_8080_anthropic": {"api_base": "http://localhost:8080", "native": True},
    "lmstudio": {"api_base": "http://localhost:1234", "native": False},
    "vllm_local": {"api_base": "http://localhost:8000", "native": False},
    "jan_local": {"api_base": "http://localhost:1337", "native": False},
}
# "native": True 表示优先走 litellm 内置 provider 路由（而非强制 custom_llm_provider=openai）
# ── MCP 运行时接线（启动连接 + 工具注入）──────────────
_MCP_REGISTRY_REF: dict[str, Any] = {}  # 可变容器，供 lifespan 访问


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


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用"""
    _diag("create_app() 被调用")

    # 先定义 lifespan（注册工具后，变量通过闭包引用）
    _lifespan_registry: "ToolRegistry | None" = None

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # ── startup ──
        _diag("lifespan: startup")
        if _lifespan_registry is not None:
            await _connect_mcp_servers(_lifespan_registry)
        yield
        # ── shutdown ──
        _diag("lifespan: shutdown")
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
        PlanAndExecuteTool(),
        McpAddServerTool(),
        McpUpdateServerTool(),
        McpDeleteServerTool(),
        McpListServersTool(),
    )
    _lifespan_registry = registry  # 供 lifespan 闭包引用

    # 注入 MCP 工具刷新回调（配置变更后自动重连+刷新工具注册）
    from core.tools.mcp_tools import set_refresh_callback
    from functools import partial
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

    # 注册 WebSocket 端点
    @app.websocket("/ws/{character_id}")
    async def websocket_endpoint(ws: WebSocket, character_id: str) -> None:
        """WebSocket 连接端点"""
        _diag(f"websocket_endpoint CALLED, char_id={character_id}")
        try:
            await ws_server.handle_connection(ws, character_id)
        except Exception as e:
            _diag(f"websocket_endpoint EXCEPTION: {e!s}")
            raise

    @app.get("/api/characters")
    async def list_characters() -> list[dict]:
        """列出可用角色 — 动态扫描 character/ 目录"""
        from core.character.loader import CharacterLoader

        loader = CharacterLoader(PROJECT_ROOT)
        char_dir = PROJECT_ROOT / "character"
        results = []
        if char_dir.exists():
            for item in sorted(char_dir.iterdir()):
                if item.is_dir():
                    info = loader.get_character_info(item.name)
                    if info["name"]:
                        results.append(info)
        return results

    @app.get("/api/health")
    async def health_check() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    # ── 确认对话框 HTTP 端点 ──
    @app.post("/api/confirm")
    async def api_confirm(session_id: str = "", action: str = "confirm") -> dict:
        """通过 HTTP 接收用户确认操作"""
        if not session_id:
            return {"status": "error", "error": "session_id required"}
        session = ws_server.get_session(session_id)
        if not session:
            return {"status": "error", "error": "session not found"}
        session.confirm_result = action
        if session.pending_confirm and not session.pending_confirm.is_set():
            session.pending_confirm.set()
        _diag(f"api_confirm: session={session_id}, action={action}")
        return {"status": "ok", "action": action}

    @app.get("/api/conversations/{character_id}")
    async def list_conversations(character_id: str) -> list[dict]:
        """列出指定角色的历史会话摘要"""
        data_dir = PROJECT_ROOT / ".AIGEME" / ".data"
        from core.config.settings import get_config
        user_id = get_config().get("user", {}).get("default_id", "local")
        conv_dir = data_dir / user_id / character_id / "conversations"
        results = []
        if conv_dir.exists():
            # 所有文件按时间合并：分卷(_001→_002)在前，主文件(conversations.json)在后
            files = sorted(conv_dir.glob("*.json"))
            split_files = sorted(f for f in files if "_" in f.stem)
            main_files = [f for f in files if "_" not in f.stem]
            for f in split_files + main_files:
                try:
                    records = json.loads(f.read_text("utf-8"))
                    if not records:
                        continue
                    last_msg = records[-1].get("data", {}).get("content", "") if records else ""
                    if results:
                        results[0]["message_count"] += len(records)
                        results[0]["last_message"] = last_msg[:100]
                        results[0]["timestamp"] = records[-1].get("timestamp", "")
                    else:
                        results.append({
                            "date": "all",
                            "message_count": len(records),
                            "last_message": last_msg[:100],
                            "timestamp": records[-1].get("timestamp", "") if records else "",
                        })
                except (json.JSONDecodeError, OSError):
                    continue
        return results

    @app.get("/api/conversations/{character_id}/{date}")
    async def get_conversation(character_id: str, date: str = "all") -> list[dict]:
        """获取完整对话记录（所有文件合并，忽略日期参数）"""
        data_dir = PROJECT_ROOT / ".AIGEME" / ".data"
        from core.config.settings import get_config
        user_id = get_config().get("user", {}).get("default_id", "local")
        conv_dir = data_dir / user_id / character_id / "conversations"
        all_records = []
        files = sorted(conv_dir.glob("*.json"))
        # 分卷在前，主文件在后，保证时间顺序
        split_files = sorted(f for f in files if "_" in f.stem)
        main_files = [f for f in files if "_" not in f.stem]
        for f in split_files + main_files:
            try:
                records = json.loads(f.read_text("utf-8"))
                all_records.extend(records)
            except (json.JSONDecodeError, OSError):
                continue
        return all_records

    # ──────────────────────────────────────────
    # 侧面板 API
    # ──────────────────────────────────────────

    @app.get("/api/characters/{character_id}/skills")
    async def get_character_skills(character_id: str) -> list[dict]:
        """获取指定角色的可用技能列表"""
        from core.tools.skill_tools import SkillManager
        manager = SkillManager(PROJECT_ROOT, character_id)
        skills = manager.list_all()
        return skills

    @app.get("/api/workspace")
    async def list_workspace(path: str = "", character_id: str = "ario") -> dict:
        """列出角色工作区文件"""
        import os
        from pathlib import Path
        from core.config.settings import get_config
        user_id = get_config().get("user", {}).get("default_id", "local")
        base = PROJECT_ROOT / ".AIGEME" / ".data" / user_id / character_id / "workspace"
        if not base.exists():
            base.mkdir(parents=True, exist_ok=True)
        if path:
            target = (base / path).resolve()
            if not str(target).startswith(str(base.resolve())):
                return {"error": "path outside workspace"}
        else:
            target = base

        results = []
        if target.exists():
            for item in sorted(target.iterdir()):
                results.append({
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else 0,
                })
        return {"path": str(target.relative_to(base)) if target != base else ".", "files": results}

    @app.get("/api/characters/{character_id}/memory")
    async def get_memory_index(character_id: str) -> dict:
        """获取指定角色的记忆索引摘要"""
        from core.config.settings import get_config
        user_id = get_config().get("user", {}).get("default_id", "local")
        memory_dir = PROJECT_ROOT / ".AIGEME" / ".data" / user_id / character_id / "memory"
        memory_file = memory_dir / "MEMORY.md"
        if memory_file.exists():
            content = memory_file.read_text("utf-8")
            return {"index": content}
        return {"index": ""}

    @app.get("/api/settings")
    async def get_settings() -> dict:
        """获取当前配置（只返回前端可读的配置项，api_key 永不返回明文）"""
        from core.config.settings import get_config
        cfg = get_config()
        llm = cfg.get("llm", {})
        raw_model = llm.get("model", "")
        # litellm 路由前缀格式: "provider/model_name"，拆分后供前端分别展示
        provider, model_name = _split_model(raw_model)
        return {
            "model": raw_model,
            "provider": provider,
            "model_name": model_name,
            "temperature": llm.get("temperature", 0.7),
            "max_tokens": llm.get("max_tokens", 4096),
            "api_base": llm.get("api_base", ""),
            "has_api_key": bool(llm.get("api_key", "")),
            "server_port": cfg.get("server", {}).get("port", 8765),
            "preserve_thinking": bool(llm.get("preserve_thinking", False)),
            # 上下文窗口（K 单位，前端显示用）
            "context_window_k": max(1, (llm.get("context_window", 131072) or 131072) // 1024),
            # 上下文压缩触发阈值（0.0~1.0）
            "token_limit_ratio": float(llm.get("token_limit_ratio", 0.8)),
            # 权限模式（存内存，不持久化）
            "permission_mode": _get_perm_mode(),
        }

    @app.get("/api/llm-providers")
    async def list_llm_providers() -> dict:
        """返回 litellm 支持的 LLM provider 列表（精选常用 + 中文说明 + 默认 api_base + 模型列表来源）

        前端用此列表生成下拉选择，模型名格式为 ``provider/model_name``，
        前缀决定 litellm 走哪个 provider 路由。
        """
        # (pid, name, desc, default_api_base, model_source)
        # model_source: "litellm"=从 litellm 动态获取, "openai"=调用 /v1/models, ""=不获取
        curated = [
            ("openai", "兼容 OpenAI",
             "OpenAI 官方 / 通用兼容（vLLM、Ollama、LM Studio 等均选此项）",
             "https://api.openai.com", "openai"),
            ("custom_openai", "自定义 OpenAI",
             "自定义 OpenAI 兼容端点",
             "", "openai"),
            ("anthropic", "Anthropic Claude",
             "思维链维持 + Prompt Caching",
             "https://api.anthropic.com", ""),
            ("deepseek", "DeepSeek",
             "DeepSeek V4 思维链适配",
             "https://api.deepseek.com", "openai"),
            ("dashscope", "阿里云百炼",
             "Qwen 思维链维持（enable_thinking）",
             "https://dashscope.aliyuncs.com/compatible-mode/v1", "openai"),
            ("bigmodel", "智谱 GLM",
             "GLM 思考模式（thinking.type + clear_thinking）",
             "https://open.bigmodel.cn/api/paas/v4", "openai"),
            ("azure", "Azure OpenAI",
             "Microsoft Azure 托管的 OpenAI",
             "https://YOUR_RESOURCE.openai.azure.com", ""),
            ("gemini", "Google Gemini",
             "Google Gemini 系列模型",
             "https://generativelanguage.googleapis.com", ""),
            # ── 本地部署 ──
            ("ollama", "Ollama (11434)",
             "Ollama 默认端口 11434",
             "http://localhost:11434", "openai"),
            ("local_8080", "本地服务 (8080)",
             "Llamafile / LocalAI / llama.cpp 默认端口 8080",
             "http://localhost:8080", "openai"),
            ("local_8080_anthropic", "本地服务 (8080) [Anthropic]",
             "llama.cpp Anthropic 协议 — 完美解决工具调用/SSE 兼容问题",
             "http://localhost:8080", ""),
            ("lmstudio", "LM Studio (1234)",
             "LM Studio 默认 API 端口 1234",
             "http://localhost:1234", "openai"),
            ("vllm_local", "vLLM (8000)",
             "vLLM 默认服务端口 8000",
             "http://localhost:8000", "openai"),
            ("jan_local", "Jan (1337)",
             "Jan 默认 API 端口 1337",
             "http://localhost:1337", "openai"),
        ]
        ok, supported = False, set()
        try:
            from litellm import LlmProviders  # type: ignore[import]
            supported = {p.value for p in LlmProviders}
            ok = True
        except Exception:
            supported = set()
        items = []
        for pid, name, desc, default_api_base, model_source in curated:
            item = {
                "id": pid,
                "name": name,
                "desc": desc,
                "default_api_base": default_api_base,
                "model_source": model_source,
                "litellm_supported": pid in supported if ok else True,
            }
            items.append(item)
        return {"providers": items, "total": len(items)}

    @app.get("/api/llm-providers/{provider_id}/models")
    async def list_provider_models(
        provider_id: str,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> dict:
        """根据 provider 的 api_base 获取可用模型列表（调用该端点的 /v1/models）

        Query params:
        - api_base: 若未传则使用 provider 默认值
        - api_key: 可选

        返回模型列表，仅适合有 /v1/models 端点的 OpenAI 兼容 provider。
        """
        provider_id = provider_id.strip().lower()
        import httpx
        import os

        # 获取默认 api_base
        if not api_base:
            api_base = PROVIDER_DEFAULTS.get(provider_id, {}).get("api_base", "")

        if not api_base:
            return {"models": []}

        # 拼接 /v1/models：用 httpx 的 URL 解析避免字符串拼接陷阱
        import httpx
        base_url = httpx.URL(api_base)
        # 检查是否已有 v1 路径段
        has_v1 = any(part == "v1" for part in base_url.path.rstrip("/").split("/"))
        if has_v1:
            models_url = api_base.rstrip("/") + "/models"
        else:
            models_url = api_base.rstrip("/") + "/v1/models"

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            # 尝试从环境变量或配置读取已保存的 API Key
            saved_key = os.environ.get("AIGEME_LLM_API_KEY", "")
            if saved_key:
                headers["Authorization"] = f"Bearer {saved_key}"
            elif api_base and ("localhost" in api_base or "127.0.0.1" in api_base):
                # 本地服务：OpenAI 兼容客户端通常需要非空 api_key，传占位符
                headers["Authorization"] = "Bearer not-needed"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(models_url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 401:
                msg = f"认证失败（401），请检查 API Key 是否正确"
            elif status == 403:
                msg = f"权限不足（403），API Key 可能无权限访问模型列表"
            elif status == 404:
                msg = f"端点不存在（404）：{models_url}"
            elif status == 429:
                msg = f"请求过于频繁（429），请稍后重试"
            else:
                msg = f"服务器返回错误（{status}），{e.response.text[:100]}"
            return {"models": [], "error": msg}
        except httpx.ConnectError:
            return {"models": [], "error": f"无法连接到 {models_url}，请确认服务是否已启动且地址正确"}
        except httpx.TimeoutException:
            return {"models": [], "error": f"连接超时：{models_url}"}
        except Exception as e:
            return {"models": [], "error": f"获取模型列表失败：{e!s}"}

        # 解析多种响应格式
        raw_models = data.get("data", [])
        if not raw_models and isinstance(data, list):
            raw_models = data

        models = []
        for m in raw_models:
            if isinstance(m, dict):
                models.append(m.get("id", m.get("name", "")))
            elif isinstance(m, str):
                models.append(m)

        models = [m for m in models if m]
        models.sort()
        return {"models": models, "count": len(models)}

    @app.put("/api/settings")
    async def update_settings(settings: dict) -> dict:
        """更新配置并持久化到 .AIGEME/local.yaml（不污染 settings.yaml）"""
        import os
        import yaml
        from core.config.settings import reload_config, _LOCAL_CONFIG_PATH

        # 读取现有的本地覆盖配置（若存在）
        local_cfg: dict = {}
        if _LOCAL_CONFIG_PATH.exists():
            try:
                with open(_LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
                    local_cfg = yaml.safe_load(f) or {}
            except Exception:
                local_cfg = {}

        llm_overrides = local_cfg.setdefault("llm", {})

        # 前端拆分提交：provider + model_name → 拼接为 "provider/model_name"
        if "model" in settings:
            llm_overrides["model"] = settings["model"]
        elif "provider" in settings or "model_name" in settings:
            from core.config.settings import get_config
            cur = get_config().get("llm", {}).get("model", "")
            from core.main import _split_model
            cur_provider, cur_name = _split_model(cur)
            provider = settings.get("provider") or cur_provider or "openai"
            model_name = settings.get("model_name")
            if model_name is None:
                model_name = cur_name
            provider = (provider or "").strip()
            model_name = (model_name or "").strip()
            llm_overrides["model"] = (
                f"{provider}/{model_name}" if provider and model_name
                else model_name or provider
            )

        if "temperature" in settings:
            llm_overrides["temperature"] = settings["temperature"]
        if "max_tokens" in settings:
            llm_overrides["max_tokens"] = settings["max_tokens"]
        if "api_base" in settings:
            llm_overrides["api_base"] = settings["api_base"]
        if "api_key" in settings:
            # 写入 Windows 用户环境变量（持久化到注册表），不写入 local.yaml
            key_val = settings["api_key"]
            if key_val:
                # 设置当前进程环境变量（立即生效）
                os.environ["AIGEME_LLM_API_KEY"] = key_val
                # 持久化到 Windows 用户环境变量（新进程生效）
                _set_user_env_var("AIGEME_LLM_API_KEY", key_val)
            else:
                # 空字符串 → 清除环境变量
                os.environ.pop("AIGEME_LLM_API_KEY", None)
                _del_user_env_var("AIGEME_LLM_API_KEY")
            # 确保 local.yaml 中也不残留 api_key（兼容旧数据）
            llm_overrides.pop("api_key", None)

        if "server_port" in settings:
            local_cfg.setdefault("server", {})["port"] = settings["server_port"]

        if "preserve_thinking" in settings:
            llm_overrides["preserve_thinking"] = bool(settings["preserve_thinking"])

        # 权限模式（仅存内存，通过 bash_tools 模块级变量持有，不持久化到 local.yaml）
        if "permission_mode" in settings:
            _set_perm_mode(settings["permission_mode"])

        if "context_window_k" in settings:
            raw = int(settings["context_window_k"]) * 1024
            llm_overrides["context_window"] = max(32768, min(1048576, raw))

        if "token_limit_ratio" in settings:
            val = float(settings["token_limit_ratio"])
            llm_overrides["token_limit_ratio"] = max(0.1, min(1.0, val))

        # 确保 .AIGEME 目录存在
        _LOCAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(_LOCAL_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(local_cfg, f, allow_unicode=True, default_flow_style=False)
            reload_config()
            # 重置 InstructorClient，使新配置（model/api_key/api_base 等）立即生效
            ws_server.reset_instructor()
            _diag("update_settings: InstructorClient reset after config save")
            return {"status": "ok", "message": "设置已保存，新配置已立即生效"}
        except Exception as e:
            return {"status": "error", "message": f"保存失败: {e!s}"}

    # 根路径返回前端页面（动态注入内容哈希，避免浏览器长期缓存旧 JS/CSS）
    import hashlib
    import re as _re
    from fastapi.responses import HTMLResponse

    def _file_hash(path: Path, length: int = 8) -> str:
        """计算文件内容的 md5 短串（8位），文件不存在则返回 '0'"""
        try:
            data = path.read_bytes()
            return hashlib.md5(data).hexdigest()[:length]
        except OSError:
            return "0"

    def _inject_version(html: str, static_root: Path) -> str:
        """将 /static/... 的 JS/CSS 引用替换为带 ?v=<hash> 的版本"""
        def _replace(m: _re.Match) -> str:
            tag_prefix = m.group(1)   # src=" 或 href="
            url = m.group(2)           # /static/js/app.js
            tag_suffix = m.group(3)   # "
            # 只处理 /static/ 前缀的本地资源
            if not url.startswith("/static/"):
                return m.group(0)
            rel = url[len("/static/"):]
            file_path = static_root / rel
            h = _file_hash(file_path)
            return f'{tag_prefix}{url}?v={h}{tag_suffix}'

        # 匹配 src="/static/..." 和 href="/static/..."
        pattern = r'((?:src|href)="?)(/static/[^"?\s]+)("?)'
        return _re.sub(pattern, _replace, html)

    @app.get("/")
    async def index() -> HTMLResponse:
        html_path = frontend_dir / "index.html"
        html = html_path.read_text(encoding="utf-8")
        html = _inject_version(html, frontend_dir)
        return HTMLResponse(
            content=html,
            headers={
                # index.html 本身：每次都向服务端验证，不允许直接用旧缓存
                "Cache-Control": "no-cache, must-revalidate",
            },
        )

    return app
