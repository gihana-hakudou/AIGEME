"""LLM Provider 与模型列表 API 路由"""

import logging

from fastapi import APIRouter
import httpx

from core.utils import PROVIDER_DEFAULTS

logger = logging.getLogger(__name__)
router = APIRouter(tags=["models"])


@router.get("/api/llm-providers")
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


@router.get("/api/llm-providers/{provider_id}/models")
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
    import os

    # 获取默认 api_base
    if not api_base:
        api_base = PROVIDER_DEFAULTS.get(provider_id, {}).get("api_base", "")

    if not api_base:
        return {"models": []}

    # 拼接 /v1/models：用 httpx 的 URL 解析避免字符串拼接陷阱
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
