"""SystemTool — 系统信息查询"""

import platform
import sys
from pathlib import Path

from core.tools.base import BaseTool


class SystemTool(BaseTool):
    """系统信息工具"""

    name = "system"
    description = "系统信息查询。支持查询编译环境和可用工具。"
    output_type = "json"

    parameters = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["env"],
                "description": "查询模式: env=系统环境信息",
            }
        },
        "required": ["mode"],
    }

    async def execute(self, mode: str = "env", **kwargs) -> dict:  # type: ignore[override]
        if mode == "env":
            return {
                "status": "ok",
                "result": {
                    "platform": platform.platform(),
                    "python_version": sys.version,
                    "python_path": sys.executable,
                    "current_directory": str(Path.cwd()),
                },
            }
        return {"status": "error", "error": f"不支持的模式: {mode}"}
