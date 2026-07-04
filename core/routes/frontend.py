"""前端页面 + 健康检查 API 路由"""

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from core.utils import PROJECT_ROOT, _inject_version

logger = logging.getLogger(__name__)
router = APIRouter(tags=["frontend"])


@router.get("/api/health")
async def health_check() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@router.get("/")
async def index() -> HTMLResponse:
    frontend_dir = PROJECT_ROOT / "frontend" / "chat"
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
