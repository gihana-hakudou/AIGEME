"""BashTool — Shell 命令执行（MVP 阶段使用基本安全规则）"""

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
    parts = command.split()
    if parts and parts[0] in ("python", "python3", "pip", "pip3"):
        parts[0] = venv_python if parts[0].startswith("python") else str(
            (PROJECT_VENV if PROJECT_VENV.exists() else _PROJECT_VENV_ALT) / "Scripts" / f"{parts[0]}.exe"
        )
        return " ".join(parts)

    # 也处理形如 "cd xxx && python" 或 "cd xxx ; python" 的场景
    for sep in ("&&", ";"):
        segments = command.split(f" {sep} ")
        changed = False
        new_segments = []
        for seg in segments:
            seg = seg.strip()
            seg_parts = seg.split()
            if seg_parts and seg_parts[0] in ("python", "python3", "pip", "pip3"):
                if seg_parts[0].startswith("python"):
                    seg_parts[0] = venv_python
                else:
                    venv_dir = PROJECT_VENV if PROJECT_VENV.exists() else _PROJECT_VENV_ALT
                    seg_parts[0] = str(venv_dir / "Scripts" / f"{seg_parts[0]}.exe")
                seg = " ".join(seg_parts)
                changed = True
            new_segments.append(seg)
        if changed:
            return f" {sep} ".join(new_segments)

    return command


# ── Phase 3: AST 解析 ──

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

    @staticmethod
    def extract_paths(segment: ParsedSegment) -> list[str]:
        """从命令参数中提取可能的文件路径（跳过指令 flag）"""
        paths = []
        for arg in segment.args:
            # 跳过指令 flag（-xxx 或 /xxx）
            if arg.startswith("-") or arg.startswith("/"):
                continue
            if "/" in arg or "\\" in arg or arg.startswith("~"):
                paths.append(arg)
            elif arg.startswith(".") and len(arg) > 1:
                paths.append(arg)
        return paths


# ── 命令白名单（Phase 3 替代旧黑名单） ──

# 绝对禁止的系统命令
ALWAYS_BLOCKED = {
    "sudo", "su", "shutdown", "reboot", "poweroff", "halt",
    "format", "mkfs", "fdisk", "dd",
    "iptables", "iptables-restore", "firewall-cmd", "ufw",
    "systemctl", "service",
    "ssh", "scp", "sftp", "rsync",
    "passwd", "useradd", "userdel",
    "mount", "umount", "modprobe", "insmod",
    "crontab", "at",
    "regedit",
}

# 需确认的命令（有写入/破坏风险）
CONFIRM_COMMANDS = {
    "rm", "del", "rmdir", "rd",
    "mv", "move", "cp", "copy",
    "mkdir", "md", "touch",
    "sed", "tee", "base64",
    "kill", "killall", "pkill",
    "wget", "curl",
}

# 自动允许的命令（只读/信息查询）
ALLOWED_COMMANDS = {
    "cd", "chdir", "pushd", "popd",
    "ls", "dir", "cat", "type", "head", "tail", "less", "more",
    "grep", "find", "wc", "sort", "uniq", "cut", "diff", "cmp",
    "pwd", "echo", "printf", "date",
    "file", "stat", "du", "df",
    "which", "where",
    "python", "python3", "pip", "pip3",
    "git", "pytest",
    "npx",
}


def _path_zone_check(path: str, is_write: bool = True) -> str:
    """同步版路径区域判定（不依赖 async chain）

    与 ZonePermissionFilter 逻辑保持一致：
    - writable → auto（读+写均允许）
    - project + read → auto（读项目代码允许）
    - project + write → confirm
    - system → blocked
    - external → confirm

    Args:
        path: 要检查的文件路径
        is_write: 是否为写操作，默认为 True（安全保守）

    返回: "auto" / "confirm" / "blocked"
    """
    p = Path(path).resolve()
    project_root = Path(__file__).parent.parent.parent

    # 先检查是否在 project root 下
    in_project = str(p).startswith(str(project_root))

    # writable 区域：必须在 project root 下 + 子目录名称匹配
    writable_names = {".AIGEME", "character", "tachi-e", "venv"}
    if in_project:
        for part in p.relative_to(project_root).parts:
            if part in writable_names:
                return "auto"

    # project 区域：读操作 auto，写操作 confirm
    if in_project:
        if is_write:
            return "confirm"  # 项目代码区写入需确认
        return "auto"  # 项目代码区读取自动允许

    # 系统路径
    system_prefixes = [r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)"]
    for sp in system_prefixes:
        if str(p).startswith(sp):
            return "blocked"

    return "confirm"


# ── 脚本解释器定义 ──
SCRIPT_INTERPRETERS: dict[str, dict] = {
    "python": {
        "aliases": ["python", "python3", "py"],
        "exec_flags": ["-c"],
        "description": "Python 解释器"
    },
    "perl": {
        "aliases": ["perl"],
        "exec_flags": ["-e"],
        "description": "Perl 解释器"
    },
    "ruby": {
        "aliases": ["ruby"],
        "exec_flags": ["-e"],
        "description": "Ruby 解释器"
    },
    "node": {
        "aliases": ["node", "nodejs", "deno", "bun"],
        "exec_flags": ["-e", "--eval"],
        "description": "JavaScript/TypeScript 解释器"
    },
    "php": {
        "aliases": ["php"],
        "exec_flags": ["-r"],
        "description": "PHP 解释器"
    },
    "powershell": {
        "aliases": ["powershell", "pwsh"],
        "exec_flags": ["-Command", "-c"],
        "description": "PowerShell 解释器"
    },
    "lua": {
        "aliases": ["lua", "luajit"],
        "exec_flags": ["-e"],
        "description": "Lua 解释器"
    },
    "tclsh": {
        "aliases": ["tclsh", "wish"],
        "exec_flags": ["-c"],
        "description": "Tcl 解释器"
    },
    "sh": {
        "aliases": ["sh", "bash", "zsh", "dash", "ksh", "fish"],
        "exec_flags": ["-c"],
        "description": "Shell 解释器"
    },
    "awk": {
        "aliases": ["awk", "gawk", "mawk", "nawk"],
        "exec_flags": ["-e"],
        "description": "Awk 文本处理工具"
    },
    "groovy": {
        "aliases": ["groovy"],
        "exec_flags": ["-e"],
        "description": "Groovy 解释器"
    },
    "scala": {
        "aliases": ["scala", "scalac"],
        "exec_flags": ["-e"],
        "description": "Scala 解释器"
    },
    "lisp": {
        "aliases": ["sbcl", "clisp", "clojure", "gosh", "racket", "guile"],
        "exec_flags": ["-e"],
        "description": "Lisp 系列解释器"
    },
    "kotlin": {
        "aliases": ["kotlin"],
        "exec_flags": ["-e"],
        "description": "Kotlin 解释器"
    },
    "swift": {
        "aliases": ["swift"],
        "exec_flags": ["-e"],
        "description": "Swift 解释器"
    },
    "rhino": {
        "aliases": ["rhino", "jrunscript", "js"],
        "exec_flags": ["-e"],
        "description": "Java/JS 脚本引擎"
    },
}

# 风险命令前缀（需确认）—— 作为深层防御 fallback
RISKY_COMMANDS = [
    "rm", "del", "git push", "git reset", "git rebase",
    "wget", "ping", "dig", "nslookup", "host",
    "traceroute", "tracepath", "mtr",
    "kill", "killall", "pkill", "nohup",
    "gcc", "g++", "clang", "clang++", "make", "cmake",
    "rustc", "go", "cargo",
    "sqlite3", "base64", "strings",
]


def _is_command_blocked(command_name: str) -> bool:
    """判断命令是否在绝对禁止集合中（提取 basename，去除 .exe）"""
    base = basename(command_name)
    if base.endswith(".exe"):
        base = base[:-4]
    return base in ALWAYS_BLOCKED


def _match_interpreter(command_name: str) -> dict | None:
    """在 SCRIPT_INTERPRETERS 中查找匹配（含别名，提取 basename，去除 .exe 后缀）"""
    name = basename(command_name).lower()
    # 去除 .exe 后缀（Windows 兼容）
    if name.endswith(".exe"):
        name = name[:-4]
    for info in SCRIPT_INTERPRETERS.values():
        if name in info["aliases"]:
            return info
    return None


def _classify_bash_op(command: str, segment: ParsedSegment) -> str:
    """判断 bash 命令的操作类型: read / write / exec"""
    cmd = segment.command
    if cmd in {"rm", "del", "rmdir", "rd", "mv", "move", "cp", "copy",
               "mkdir", "md", "touch", "tee", "sed"}:
        return "write"
    if cmd in {"cat", "head", "tail", "less", "more", "type",
               "ls", "dir", "grep", "find", "wc", "sort", "uniq", "cut", "diff", "cmp",
               "file", "stat", "du", "df", "which", "where"}:
        return "read"
    return "exec"


def _check_command_risk(command: str) -> str:
    """统一安全检查：AST 解析 → 命令白名单 → 路径区域检查"""
    if not command or not command.strip():
        return "auto"

    parsed = BashASTParser.parse(command)

    for segment in parsed:
        cmd = segment.command

        # 语法错误
        if cmd == "__SYNTAX_ERROR__":
            return "blocked"

        # 跳过环境变量赋值（$env:VAR='val'、VAR=val 等），它们是安全的前置设置
        if "=" in cmd and (cmd.startswith("$") or cmd[0].isalpha() and cmd.split("=", 1)[0].isupper()):
            continue

        # 1. 绝对禁止
        if cmd in ALWAYS_BLOCKED:
            return "blocked"

        # 2. 项目 venv 或 writable 区域内的 python/pip → auto
        if cmd in ("python", "python3", "pip", "pip3"):
            # 兼容旧检查：venv 路径（支持 venv 和 .venv）
            if str(PROJECT_VENV) in command or str(_PROJECT_VENV_ALT) in command:
                continue  # 跳过后续检查
            # 新检查：命令中的路径是否在 writable zone 内
            paths = BashASTParser.extract_paths(segment)
            in_writable = False
            for fp in paths:
                zone = _path_zone_check(fp, is_write=True)  # 保守假设写操作
                if zone == "auto":
                    in_writable = True
                    break
            if in_writable:
                continue  # writable 区域内自动放行
            # 不 return confirm — 让后续 ALLOWED_COMMANDS 判断接管
            # python 已在 ALLOWED_COMMANDS 中，会被自动放行

        # 3. 提取路径参数 → 同步路径区域检查
        paths = BashASTParser.extract_paths(segment)
        is_write = _classify_bash_op(command, segment) == "write"
        for fp in paths:
            zone_result = _path_zone_check(fp, is_write=is_write)
            if zone_result == "blocked":
                return "blocked"
            if zone_result == "confirm":
                return "confirm"
            # auto → 继续检查其他路径

        # 4. 命令白名单
        if cmd in CONFIRM_COMMANDS:
            return "confirm"
        if cmd not in ALLOWED_COMMANDS:
            return "confirm"

    return "auto"


class BashTool(BaseTool):
    """Shell 命令执行工具"""

    name = "bash"
    description = "执行 shell 命令。项目 venv 内自动允许；系统环境修改和风险操作需确认。"
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
                return {
                    "status": "error",
                    "error": f"命令执行失败: {err_detail[:500]}",
                    "result": {
                        "stdout": stdout[-8000:],
                        "stderr": stderr[-4000:],
                        "returncode": result.returncode,
                    },
                }

            return {
                "status": "ok",
                "result": {
                    "stdout": stdout[-8000:],
                    "stderr": stderr[-4000:],
                    "returncode": result.returncode,
                },
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": f"命令执行超时（{timeout}秒）。可通过 timeout 参数调整超时时间：bash(command='{command[:200]}', timeout=120)"}
        except Exception as e:
            return {"status": "error", "error": f"执行失败: {e!s}"}

    async def execute(self, command: str, timeout: int = 30, **kwargs) -> dict:  # type: ignore[override]
        # 用户已确认或强制 bypass 时跳过风险检查
        if kwargs.get("_confirmed") or kwargs.get("_force"):
            return await self._run_command(command, timeout=timeout)

        risk = _check_command_risk(command)

        if risk == "blocked":
            cmd_name = command.strip().split()[0] if command.strip() else command
            return {"status": "blocked", "reason": f"'{cmd_name}' 被安全策略阻止，不允许执行"}

        if risk == "confirm":
            return {"status": "needs_confirm", "operation": f"执行命令需用户确认: {command}"}

        return await self._run_command(command, timeout=timeout)
