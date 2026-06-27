"""角色加载器 — 读取 soul.md / identity.md / expressions.yaml"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CharacterDef:
    """角色定义"""

    id: str
    name: str = ""
    description: str = ""
    soul: str = ""
    identity: str = ""
    expressions: dict[str, str] = field(default_factory=dict)
    skills: list[str] = field(default_factory=list)
    tachie_dir: str = ""
    speak_weight: float = 1.0
    speak_weight_label: str = "normal"


class CharacterLoader:
    """角色文件加载器"""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    def load_character(self, char_id: str) -> CharacterDef | None:
        """加载指定角色的所有文件"""
        char_dir = self._project_root / "character" / char_id
        if not char_dir.exists():
            return None

        char = CharacterDef(id=char_id)

        # soul.md
        soul_path = char_dir / "soul.md"
        if soul_path.exists():
            char.soul = soul_path.read_text("utf-8")

        # identity.md
        identity_path = char_dir / "identity.md"
        if identity_path.exists():
            char.identity = identity_path.read_text("utf-8")

        # expressions.yaml
        expr_path = char_dir / "expressions.yaml"
        if expr_path.exists():
            try:
                data = yaml.safe_load(expr_path.read_text("utf-8"))
                if isinstance(data, dict):
                    # 兼容两种格式：
                    #   expressions: { default: neutral.png }  ← 嵌套
                    #   default: neutral.png                  ← 扁平
                    raw = data.get("expressions", data)
                    if isinstance(raw, dict):
                        char.expressions = raw
            except yaml.YAMLError:
                pass

        # config.yaml (角色注册信息 + speak_weight 等)
        config_path = char_dir / "config.yaml"
        if config_path.exists():
            try:
                config_data = yaml.safe_load(config_path.read_text("utf-8"))
                if isinstance(config_data, dict):
                    char.name = str(config_data.get("name", char_id))
                    char.description = str(config_data.get("description", ""))
                    char.skills = config_data.get("skills", [])
                    char.tachie_dir = str(config_data.get("tachie_dir", f"tachi-e/{char_id}"))
                    sw = config_data.get("speak_weight", 1.0)
                    if isinstance(sw, (int, float)) and sw >= 0:
                        char.speak_weight = float(sw)
                    char.speak_weight_label = str(config_data.get("speak_weight_label", "normal"))
            except (yaml.YAMLError, ValueError):
                pass

        # 从 settings.yaml 获取角色额外信息（向后兼容，config.yaml 优先）
        from core.config.settings import get_config
        config = get_config()
        for c in config.get("characters", []):
            if c.get("id") == char_id:
                if not char.name or char.name == char_id:
                    char.name = c.get("name", char_id)
                if not char.description:
                    char.description = c.get("description", "")
                if not char.skills:
                    char.skills = c.get("skills", [])
                if not char.tachie_dir:
                    char.tachie_dir = c.get("tachie_dir", f"tachi-e/{char_id}")

        return char

    def get_character_info(self, char_id: str) -> dict:
        """获取角色基本信息（优先从 config.yaml，降级到 identity.md / soul.md）"""
        char_dir = self._project_root / "character" / char_id
        info: dict = {"id": char_id, "name": char_id, "description": "", "avatar": ""}

        # config.yaml 优先
        config_path = char_dir / "config.yaml"
        if config_path.exists():
            try:
                cfg = yaml.safe_load(config_path.read_text("utf-8"))
                if isinstance(cfg, dict):
                    info["name"] = str(cfg.get("name", char_id))
                    info["description"] = str(cfg.get("description", ""))
                    # tachie_dir：从 config.yaml 读取，默认为 tachi-e/<char_id>
                    info["tachie_dir"] = str(cfg.get("tachie_dir", f"tachi-e/{char_id}"))
            except (yaml.YAMLError, ValueError):
                pass

        # 若 config.yaml 未设置 tachie_dir，使用默认值
        if "tachie_dir" not in info:
            info["tachie_dir"] = f"tachi-e/{char_id}"

        # 降级：从 identity.md 提取名字
        if info["name"] == char_id:
            identity_path = char_dir / "identity.md"
            if identity_path.exists():
                content = identity_path.read_text("utf-8")
                for line in content.splitlines():
                    if line.startswith("# "):
                        info["name"] = line[2:].strip()
                        break

        # 降级：从 soul.md 提取描述
        if not info["description"]:
            soul_path = char_dir / "soul.md"
            if soul_path.exists():
                content = soul_path.read_text("utf-8").strip()
                paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
                if paragraphs:
                    info["description"] = paragraphs[0][:200]

        # expressions.yaml
        expr_path = char_dir / "expressions.yaml"
        if expr_path.exists():
            try:
                expr_data = yaml.safe_load(expr_path.read_text("utf-8"))
                if isinstance(expr_data, dict):
                    # 兼容两种格式：嵌套 {expressions: {...}} 或扁平 {default: ...}
                    raw = expr_data.get("expressions", expr_data)
                    if isinstance(raw, dict):
                        info["expressions"] = raw
            except yaml.YAMLError:
                pass

        # avatar: 优先从 expressions 映射取 default 实际文件名，路径基于 tachie_dir
        avatar_file = "default.png"
        expr_map = info.get("expressions", {})
        default_expr = expr_map.get("default", "")
        if default_expr:
            avatar_file = default_expr
        tachie_dir = info.get("tachie_dir", f"tachi-e/{char_id}")
        avatar_path = self._project_root / tachie_dir / avatar_file
        if avatar_path.exists():
            info["avatar"] = f"/{tachie_dir}/{avatar_file}"

        return info
