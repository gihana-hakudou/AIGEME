"""系统环境检测与 system_info.md 自动生成"""

import platform
from pathlib import Path


def generate_system_info(project_root: Path) -> str:
    """检测系统环境并写入 .AIGEME/.data/system/system_info.md

    Args:
        project_root: 项目根目录路径

    Returns:
        生成的 Markdown 文本
    """
    info: list[str] = []
    info.append("# 系统环境")
    info.append("")

    # 操作系统
    info.append("## 操作系统")
    info.append(f"- {platform.platform()}")
    info.append("")

    # Python
    venv_python = project_root / "venv" / "Scripts" / "python.exe"
    info.append("## Python")
    info.append(f"- {venv_python}")
    info.append("")

    text = "\n".join(info)

    # 写入文件
    output_dir = project_root / ".AIGEME" / ".data" / "system"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "system_info.md"
    output_path.write_text(text, encoding="utf-8")

    return text