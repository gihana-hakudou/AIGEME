"""配置/设置 API 路由（含确认端点）"""

import logging
import os

from fastapi import APIRouter, Request
import yaml

from core.config.settings import reload_config, _LOCAL_CONFIG_PATH
from core.utils import (
    PROJECT_ROOT,
    _split_model,
    _get_perm_mode,
    _set_perm_mode,
    _set_user_env_var,
    _del_user_env_var,
    diag,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])


@router.get("/api/settings")
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


@router.put("/api/settings")
async def update_settings(settings: dict, request: Request) -> dict:
    """更新配置并持久化到 .AIGEME/local.yaml（不污染 settings.yaml）"""
    ws_server = request.app.state.ws_server

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
        diag("update_settings: InstructorClient reset after config save")
        return {"status": "ok", "message": "设置已保存，新配置已立即生效"}
    except Exception as e:
        return {"status": "error", "message": f"保存失败: {e!s}"}


@router.post("/api/confirm")
async def api_confirm(request: Request, session_id: str = "", action: str = "confirm") -> dict:
    """通过 HTTP 接收用户确认操作"""
    if not session_id:
        return {"status": "error", "error": "session_id required"}
    ws_server = request.app.state.ws_server
    session = ws_server.get_session(session_id)
    if not session:
        return {"status": "error", "error": "session not found"}
    session.confirm_result = action
    if session.pending_confirm and not session.pending_confirm.is_set():
        session.pending_confirm.set()
    diag(f"api_confirm: session={session_id}, action={action}")
    return {"status": "ok", "action": action}


@router.get("/api/workspace")
async def list_workspace(path: str = "", character_id: str = "ario") -> dict:
    """列出角色工作区文件"""
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
