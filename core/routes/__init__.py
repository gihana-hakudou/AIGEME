"""API 路由聚合 — 声明为 FastAPI Blueprint 风格的 APIRouter"""

from .characters import router as characters_router
from .conversations import router as conversations_router
from .models import router as models_router
from .settings import router as settings_router
from .frontend import router as frontend_router

__all__ = [
    "characters_router",
    "conversations_router",
    "models_router",
    "settings_router",
    "frontend_router",
]
