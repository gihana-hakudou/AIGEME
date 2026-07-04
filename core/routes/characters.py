"""角色相关 API 路由"""

import logging
from pathlib import Path

from fastapi import APIRouter, Request

from core.utils import PROJECT_ROOT

logger = logging.getLogger(__name__)
router = APIRouter(tags=["characters"])


@router.get("/api/characters")
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


@router.get("/api/characters/{character_id}/skills")
async def get_character_skills(character_id: str) -> list[dict]:
    """获取指定角色的可用技能列表"""
    from core.tools.skill_tools import SkillManager

    manager = SkillManager(PROJECT_ROOT, character_id)
    skills = manager.list_all()
    return skills


@router.get("/api/characters/{character_id}/memory")
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
