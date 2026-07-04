"""系统环境检测与 system_info.md 自动生成"""

import os
import platform
from pathlib import Path


def _find_bash() -> str | None:
    """查找系统中的 bash，优先 Git Bash"""
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Git\bin\bash.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    # PATH 中查找（兼容 WSL、MSYS2、Cygwin 等）
    try:
        import subprocess
        result = subprocess.run(
            ["where", "bash"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            path = result.stdout.strip().splitlines()[0]
            if path:
                return path
    except Exception:
        pass
    return None


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

    # Shell（bash.exe 检测）
    bash_path = _find_bash()
    info.append("## Shell 命令执行")
    if bash_path:
        info.append(f"- ✅ 系统 Bash：`{bash_path}`")
        info.append("- BashTool 将使用原生 bash 执行命令")
    else:
        info.append("- ❌ 系统未检测到 bash")
        info.append("- BashTool 将降级为使用 PowerShell 执行命令")
    info.append("")

    text = "\n".join(info)

    # 写入文件
    output_dir = project_root / ".AIGEME" / ".data" / "system"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "system_info.md"
    output_path.write_text(text, encoding="utf-8")

    return text