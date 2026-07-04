"""工具函数与常量 — 从 main.py 提取，避免循环导入"""

import hashlib
import logging
import re as _re
from pathlib import Path

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
    import subprocess
    import sys

    # 使用 setx 写入 HKCU\Environment
    if sys.platform == "win32":
        subprocess.run(
            ["setx", name, value],
            capture_output=True, text=True, timeout=10,
        )


def _del_user_env_var(name: str) -> None:
    """从 Windows 注册表删除用户环境变量"""
    import subprocess
    import sys

    if sys.platform == "win32":
        subprocess.run(
            ["reg", "delete", "HKCU\\Environment", "/v", name, "/f"],
            capture_output=True, text=True, timeout=10,
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


# === 诊断日志 ===
from core.engine.diag_logger import diag as _base_diag  # noqa: E402


def diag(msg: str) -> None:
    """写诊断日志（来源标记为 main）"""
    _base_diag(msg, source="main")


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
