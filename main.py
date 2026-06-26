"""AIGEME — ASGI 入口点"""

import os
from pathlib import Path

import yaml
import uvicorn
from core.main import create_app

# 从 settings.yaml 读取 LLM 配置并设环境变量（供 litellm 读取）
_settings_path = Path(__file__).parent / "config" / "settings.yaml"
if _settings_path.exists():
    try:
        _cfg = yaml.safe_load(_settings_path.read_text(encoding="utf-8"))
        _llm = _cfg.get("llm", {})
        if _llm.get("api_base"):
            os.environ.setdefault("OPENAI_BASE_URL", _llm["api_base"])
        if _llm.get("api_key"):
            os.environ.setdefault("OPENAI_API_KEY", _llm["api_key"])
    except Exception:
        pass

# 自动生成 system_info.md
_project_root = Path(__file__).parent
try:
    from core.system_info import generate_system_info
    generate_system_info(_project_root)
except Exception:
    pass

app = create_app()

if __name__ == "__main__":
    import threading
    import time
    import webbrowser
    from urllib import request

    url = "http://127.0.0.1:8765/"

    def _open_when_ready():
        for _ in range(60):
            try:
                request.urlopen(url, timeout=1)
                webbrowser.open(url)
                return
            except Exception:
                time.sleep(1)

    threading.Thread(target=_open_when_ready, daemon=True).start()

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8765,
        reload=True,
        reload_dirs=["./core", "./config"],
    )
    