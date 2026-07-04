"""BashTool — Shell 命令执行（重构版：按权限模式 + 写路径检测）"""

import os
import re
import shlex
import subprocess
import sys
import locale
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from os.path import basename

from core.tools.base import BaseTool


# ── Helper ────────────────────────────────────────────────────────────────────

def _decode_bytes(data: bytes) -> str:
    """尝试多种编码解码 bytes，确保 PowerShell 等非 UTF-8 输出也能正常显示"""
    if not data:
        return ""
    for enc in ("utf-8", locale.getpreferredencoding(False), "gbk", "utf-16-le"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # 兜底：丢弃无法解码的字节
    return data.decode("utf-8", errors="replace")

# 项目 venv 路径（用于判断免确认权限）
_PROJECT_ROOT = Path(__file__).parent.parent.parent
PROJECT_VENV = _PROJECT_ROOT / "venv"
# 也检查 .venv（fallback）
_PROJECT_VENV_ALT = _PROJECT_ROOT / ".venv"


def _resolve_python_to_venv(command: str) -> str:
    """将命令中的 python/python3 替换为项目 venv 的完整路径

    确保 bash 工具使用正确的 Python 环境和依赖包，而非系统全局 Python。
    """
    venv_python = None
    for v in (PROJECT_VENV, _PROJECT_VENV_ALT):
        p = v / "Scripts" / "python.exe"
        if p.exists():
            venv_python = str(p.resolve())
            break
    if not venv_python:
        return command  # 找不到 venv，原样返回

    # 替换命令开头的 python/python3/pip/pip3
    # 使用 shlex.split(posix=False) 正确处理 Windows 路径中的引号和反斜杠
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        return command  # 无法分词时原样返回
    if parts and parts[0] in ("python", "python3", "pip", "pip3"):
        parts[0] = venv_python if parts[0].startswith("python") else str(
            (PROJECT_VENV if PROJECT_VENV.exists() else _PROJECT_VENV_ALT) / "Scripts" / f"{parts[0]}.exe"
        )
        return shlex.join(parts)

    # 也处理形如 "cd xxx && python" 或 "cd xxx ; python" 的场景
    for sep in ("&&", ";"):
        segments = command.split(f" {sep} ")
        changed = False
        new_segments = []
        for seg in segments:
            seg = seg.strip()
            try:
                seg_parts = shlex.split(seg, posix=False)
            except ValueError:
                seg_parts = seg.split()  # fallback: 无法用 shlex 时用简单分词
            if seg_parts and seg_parts[0] in ("python", "python3", "pip", "pip3"):
                if seg_parts[0].startswith("python"):
                    seg_parts[0] = venv_python
                else:
                    venv_dir = PROJECT_VENV if PROJECT_VENV.exists() else _PROJECT_VENV_ALT
                    seg_parts[0] = str(venv_dir / "Scripts" / f"{seg_parts[0]}.exe")
                seg = shlex.join(seg_parts)
                changed = True
            new_segments.append(seg)
        if changed:
            return f" {sep} ".join(new_segments)

    return command


# ── AST 解析 ──

@dataclass
class ParsedSegment:
    command: str
    args: list[str] = field(default_factory=list)


class BashASTParser:
    """用 shlex 安全分词，结构化解析命令"""

    @staticmethod
    def parse(command: str) -> list[ParsedSegment]:
        """返回命令段列表（管道分隔）"""
        try:
            # Windows 路径使用反斜杠 \，shlex POSIX 模式会将其视为转义字符
            # 导致路径被破坏（如 F:\设计 → F设计）
            # 在 Windows 上使用 posix=False 保留反斜杠原样
            tokens = shlex.split(command, posix=(os.name != "nt"))
        except ValueError:
            return [ParsedSegment(command="__SYNTAX_ERROR__")]

        segments = []
        current_args = []
        for token in tokens:
            if token == "|":
                if current_args:
                    segments.append(ParsedSegment(
                        command=current_args[0],
                        args=current_args[1:]
                    ))
                    current_args = []
            else:
                current_args.append(token)
        if current_args:
            segments.append(ParsedSegment(
                command=current_args[0],
                args=current_args[1:]
            ))
        return segments


# ── 安全检测函数 ──

def _matches_destructive(command: str) -> bool:
    """检测毁灭级命令（所有模式下都拦截）"""
    cmd_lower = command.strip().lower()
    # rm -rf / 或变体
    if re.search(r'\brm\s+[-/].*?[/\\]|del\s+/[fsq]', cmd_lower):
        return True
    # format / fdisk / dd
    if re.search(r'\b(format|fdisk|dd)\b', cmd_lower):
        return True
    return False


def _extract_write_targets(command: str) -> list[str]:
    """从 bash 命令中提取所有可能会写文件的目标路径。

    检测以下模式：
    1. > / >> shell 重定向（排除 2>&1 这种 fd 重定向）
    2. 管道 tee：echo data | tee file
    3. 写入类命令的参数路径
    """
    targets = []

    # 1. > 和 >> 重定向
    # 模式：> path 或 >> path，但不匹配 a>b（无空格）或 >&1
    for m in re.finditer(r'(?<!\d)(>){1,2}\s*([^\s|&;<>()]+)', command):
        path = m.group(2).strip()
        if path and not path.startswith('&'):
            targets.append(path)

    # 2. 管道 tee
    for m in re.finditer(r'\|\s*tee\s+([^\s|&;<>()]+)', command):
        targets.append(m.group(1).strip())

    # 3. 写入类命令的参数
    write_cmds = {'cp', 'mv', 'rm', 'del', 'copy', 'move', 'rename', 'ren',
                  'mkdir', 'md', 'touch', 'tee', 'sed', 'erase', 'rd', 'rmdir'}
    parsed = BashASTParser.parse(command)
    for seg in parsed:
        if seg.command in write_cmds:
            for arg in seg.args:
                if not arg.startswith('-') and not arg.startswith('/'):
                    targets.append(arg)

    return targets


def _is_inline_script(command: str) -> bool:
    """检测是否为脚本解释器 + -c/-e 内联代码执行"""
    script_flags = {'-c', '-e', '--eval', '-Command'}
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        # 语法错误（如未闭合引号），无法判断，安全起见视为 inline
        parts = command.split()
    for i, part in enumerate(parts):
        if part in script_flags and i + 1 < len(parts):
            # 后面有代码参数
            return True
    return False


def _check_protected_path(targets: list[str]) -> bool:
    """检查目标路径中是否有 protected 路径（core/ 或 .git/）"""
    for t in targets:
        p = t.replace('\\', '/')
        # 直接匹配
        if p.startswith('core/') or p.startswith('.git/'):
            return True
        if p == 'core' or p == '.git':
            return True
        if p.startswith('./core/') or p.startswith('./.git/'):
            return True
        # 绝对路径或跨级路径
        try:
            abs_p = Path(os.path.abspath(t)).resolve()
            rel = abs_p.relative_to(_PROJECT_ROOT)
            rel_str = str(rel).replace('\\', '/')
            if rel_str.startswith('core/') or rel_str.startswith('.git/'):
                return True
        except (ValueError, RuntimeError):
            pass
    return False


def _check_command_risk(command: str, mode: str = "normal") -> str:
    """统一安全检查

    Args:
        command: 要执行的 shell 命令
        mode: 权限模式（full_auto / normal / restricted）

    Returns:
        "auto" / "confirm" / "blocked"
    """
    if not command or not command.strip():
        return "auto"

    # 1. 毁灭级命令（所有模式）
    if _matches_destructive(command):
        return "blocked"

    # 2. 写保护路径检测
    targets = _extract_write_targets(command)
    if _check_protected_path(targets):
        return "blocked"

    # 3. RESTRICTED → bash 不可用
    if mode == "restricted":
        return "blocked"

    # 4. 脚本解释器内联代码 → NORMAL 下需确认
    if mode == "normal" and _is_inline_script(command):
        return "confirm"

    # 5. 其他 → 自动放行
    return "auto"


# ── 模块级权限模式（由前端设置更新，默认 NORMAL） ──

_current_mode: str = "normal"


def set_permission_mode(mode: str) -> None:
    """设置全局权限模式（由 WebSocket RPC 调用）"""
    global _current_mode
    _current_mode = mode


def get_permission_mode() -> str:
    """获取当前权限模式"""
    global _current_mode
    return _current_mode


# ── BashTool ──

class BashTool(BaseTool):
    """Shell 命令执行工具"""

    name = "bash"
    description = (
        "执行 shell 命令。支持运行脚本、管理文件、安装依赖等。"
        "项目核心文件（core/ 目录和 .git/ 目录）受保护，不允许被修改或删除。"
        "高危操作（如格式化磁盘）会被自动拦截。"
    )
    output_type = "bash"

    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "timeout": {
                "type": "integer",
                "description": "执行超时秒数，默认 30。浏览器脚本、网络请求等长时间操作建议设为 120 或更高",
                "default": 30,
            },
        },
        "required": ["command"],
    }

    async def _run_command(self, command: str, timeout: int = 30) -> dict:
        """执行 shell 命令并返回结果（已通过安全检查）"""
        try:
            # 将 python/python3 解析为项目 venv 的 Python，确保使用正确的环境和包
            command = _resolve_python_to_venv(command)
            _project_root = Path(__file__).parent.parent.parent.resolve()

            if sys.platform == "win32":
                # PowerShell < 7 不支持 &&，替换为 ; （顺序执行，语义最接近）
                ps_command = command.replace(" && ", " ; ").replace("\t&&\t", " ; ").replace("\n&&\n", " ;\n")
                # 行首的 && 也处理
                ps_command = re.sub(r"^&& ", "", ps_command, flags=re.MULTILINE)
                # *>&1 将 PowerShell 所有输出流（含解析器错误）重定向到 stdout
                _cmd = ["powershell", "-NoProfile", "-Command", f"{ps_command} *>&1"]
                # 设置编码环境变量，避免 GBK 无法处理 Python 子进程输出的 Unicode（如 🐴 标记）
                _env = os.environ.copy()
                _env["PYTHONIOENCODING"] = "utf-8"
                # 使用 raw bytes + 手动解码（errors='replace' 兜底），避免 text=True 在
                # PowerShell 非标准编码下静默吞掉 stderr
                result = subprocess.run(
                    _cmd,
                    capture_output=True,
                    timeout=timeout,
                    env=_env,
                )
                raw_stdout = result.stdout or b""
                raw_stderr = result.stderr or b""
                # 依次尝试 UTF-8、系统编码、GBK 解码
                stdout = _decode_bytes(raw_stdout)
                stderr = _decode_bytes(raw_stderr)
            else:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                stdout = result.stdout or ""
                stderr = result.stderr or ""

            # 检查退出码 — 非零表示命令执行失败
            if result.returncode != 0:
                err_detail = (stderr or stdout or "").strip()
                if not err_detail:
                    err_detail = f"退出码: {result.returncode}"
                # 失败时返回 stderr，截断到 10000 字符
                error_text = stderr.strip() or stdout.strip() or ""
                if not error_text:
                    error_text = f"退出码: {result.returncode}"
                return {
                    "status": "error",
                    "error": error_text,
                    "result": error_text[:10000],
                }

            # 成功：直接返回纯文本 stdout（无格式包装）
            output = stdout.strip() or stderr.strip() or "(命令执行成功，无输出)"
            return {
                "status": "ok",
                "result": output[-10000:],
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": f"命令执行超时（{timeout}秒）。可通过 timeout 参数调整超时时间：bash(command='{command[:200]}', timeout=120)"}
        except Exception as e:
            return {"status": "error", "error": f"执行失败: {e!s}"}

    async def execute(self, command: str, timeout: int = 30, **kwargs) -> dict:  # type: ignore[override]
        # 用户已确认或强制 bypass 时跳过风险检查
        if kwargs.get("_confirmed") or kwargs.get("_force"):
            return await self._run_command(command, timeout=timeout)

        risk = _check_command_risk(command, mode=_current_mode)

        if risk == "blocked":
            cmd_name = command.strip().split()[0] if command.strip() else command
            return {"status": "blocked", "reason": f"'{cmd_name}' 被安全策略阻止，不允许执行"}

        if risk == "confirm":
            return {"status": "needs_confirm", "operation": f"执行命令需用户确认: {command}"}

        return await self._run_command(command, timeout=timeout)
