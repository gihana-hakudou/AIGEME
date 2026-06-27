"""SkillTool — 技能搜索与使用（SKILL.md 文件扫描）"""

import re
from pathlib import Path

import yaml

from core.tools.base import BaseTool


class SkillManager:
    """技能管理器 — 扫描 .skill 目录下的技能"""

    def __init__(self, project_root: Path, char_id: str) -> None:
        self._skill_dirs = [
            project_root / ".AIGEME" / ".skill",
            project_root / "character" / char_id / ".skill",
        ]

    async def search(self, query: str) -> dict:
        """遍历所有 skill_dirs，搜索匹配 name/description 的技能（支持多关键词，空格分隔，任一匹配即返回）"""
        results = []
        keywords = [kw.strip().lower() for kw in query.split() if kw.strip()]
        if not keywords:
            # 无关键词时返回全部技能
            for skill_dir in self._skill_dirs:
                if not skill_dir.exists():
                    continue
                for item in skill_dir.iterdir():
                    if item.is_dir() and (item / "SKILL.md").exists():
                        name, desc = self._parse_metadata(item / "SKILL.md")
                        results.append({"name": name, "description": desc})
            return {"status": "ok", "result": {"count": len(results), "results": results}}

        for skill_dir in self._skill_dirs:
            if not skill_dir.exists():
                continue
            for item in skill_dir.iterdir():
                if item.is_dir() and (item / "SKILL.md").exists():
                    name, desc = self._parse_metadata(item / "SKILL.md")
                    name_lower = name.lower()
                    desc_lower = desc.lower()
                    # 任一关键词匹配即命中（OR 匹配）
                    for kw in keywords:
                        if kw in name_lower or kw in desc_lower:
                            results.append({"name": name, "description": desc})
                            break
        return {"status": "ok", "result": {"count": len(results), "results": results}}

    async def use(self, name: str) -> dict:
        """读取并返回 SKILL.md 完整内容"""
        for skill_dir in self._skill_dirs:
            if not skill_dir.exists():
                continue
            for item in skill_dir.iterdir():
                if item.is_dir() and (item / "SKILL.md").exists():
                    n, _ = self._parse_metadata(item / "SKILL.md")
                    if n == name:
                        content = (item / "SKILL.md").read_text(encoding="utf-8")
                        return {"status": "ok", "result": {"name": name, "content": content}}
        return {"status": "error", "error": f"未找到技能: {name}"}

    def list_all(self) -> list[dict]:
        """列出所有可用技能"""
        skills = []
        for skill_dir in self._skill_dirs:
            if not skill_dir.exists():
                continue
            for item in skill_dir.iterdir():
                if item.is_dir() and (item / "SKILL.md").exists():
                    name, desc = self._parse_metadata(item / "SKILL.md")
                    skills.append({"name": name, "description": desc})
        return skills

    @staticmethod
    def _parse_metadata(skill_md: Path) -> tuple[str, str]:
        """解析 SKILL.md 的 front matter，返回 (name, description)"""
        try:
            content = skill_md.read_text(encoding="utf-8")
            match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
            if match:
                meta = yaml.safe_load(match.group(1))
                return meta.get("name", skill_md.parent.name), meta.get("description", "")
            return skill_md.parent.name, ""
        except Exception:
            return skill_md.parent.name, ""


class SkillTool(BaseTool):
    """技能操作工具"""

    name = "skill"
    description = "技能系统 — 搜索可用技能或查看技能文档。使用时返回 SKILL.md 内容作为参考，不会自动执行任何操作，需要你自己按文档描述调用其他工具。"

    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["search", "use"],
                "description": "search=搜索可用技能（返回匹配的技能名+描述列表），use=查看指定技能的完整SKILL.md文档（仅返回文档文本，需要你自己按文档说明调用其他工具）",
            },
            "query": {
                "type": "string",
                "description": "search操作用：搜索关键词，多个关键词用空格分隔（如 搜索词: '文件 搜索'）。任一关键词匹配技能名或描述即命中",
            },
            "name": {
                "type": "string",
                "description": "use操作用：要使用的技能名（通过search查询到的name字段值）",
            },
        },
        "required": ["operation"],
    }
    output_type = "skill_content"

    def __init__(self) -> None:
        super().__init__()
        self._manager: SkillManager | None = None

    def set_manager(self, manager: SkillManager) -> None:
        """注入 SkillManager 实例"""
        self._manager = manager

    async def execute(  # type: ignore[override]
        self,
        operation: str | None = None,
        query: str | None = None,
        name: str | None = None,
        **kwargs,
    ) -> dict:
        if not self._manager:
            return {"status": "ok", "result": {"count": 0, "results": []}}
        if operation == "search":
            return await self._manager.search(query or "")
        if operation == "use":
            return await self._manager.use(name or "")
        return {"status": "error", "error": f"未知操作: {operation}"}
