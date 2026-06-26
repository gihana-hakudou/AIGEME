"""Permission Framework — 基础权限类 + 具体过滤器（Phase 1 + Phase 2 完整实现）

提供两级安全抽象：
1. 基础设施层：PermissionVerdict / PermissionFilter / PermissionChain
2. 具体过滤器层：RequireConfirmFilter / BlocklistFilter / PathScopeFilter

执行链路：RateLimit → PermissionChain(包含多个PermissionFilter) → Tool查找 → 执行
"""

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
import os


# ── 基础设施 ──────────────────────────────────────────────


@dataclass
class PermissionVerdict:
    """权限判定结果"""

    action: str = ""
    allow: bool = True
    reason: str = ""
    require_confirm: bool = False


class PermissionFilter:
    """权限过滤器基类"""

    async def check(self, tool_name: str, args: dict, context: dict) -> PermissionVerdict:
        """检查工具调用是否允许。默认通过（no-op）。"""
        return PermissionVerdict(allow=True)


class PermissionChain:
    """权限过滤器链 — 按序检查，任一拒绝即终止"""

    def __init__(self) -> None:
        self._filters: list[PermissionFilter] = []

    def add(self, filter: PermissionFilter) -> None:
        """注册过滤器"""
        self._filters.append(filter)

    async def check(self, tool_name: str, args: dict, context: dict) -> PermissionVerdict:
        """沿过滤器链依次检查。

        短路规则：
        - 拒绝（allow=False）→ 立即返回，后续过滤器不执行
        - 需确认（require_confirm=True）→ 立即返回，不继续执行后续过滤器
          因为一旦某过滤器标记为需确认，就不应再被后续过滤器拒绝或再次确认，
          同时也避免后续过滤器将该操作降级为"确认即可"但实际已被拒绝。
        """
        for f in self._filters:
            verdict = await f.check(tool_name, args, context)
            if not verdict.allow or verdict.require_confirm:
                return verdict
        return PermissionVerdict(allow=True)


# ── 规则匹配工具 ─────────────────────────────────────────


def _match_rule(tool_name: str, args: dict, rule: dict) -> str | None:
    """通用规则匹配：检查 tool_name + args 是否匹配一条规则。

    Args:
        tool_name: 当前调用的工具名
        args: 当前调用的参数字典
        rule: 规则字典，支持以下键：
            - tool: 工具名 fnmatch 模式（默认 "*"）
            - args_contain: dict，要求 args 包含指定键值对（AND）
            - args_match: dict，要求 args 的字符串值匹配 fnmatch 模式（AND）
            - reason: 匹配时返回的理由

    Returns:
        匹配则返回 reason（非空字符串），不匹配返回 None。
    """
    # 1. 工具名匹配
    tool_pattern = rule.get("tool", "*")
    if not fnmatch(tool_name, tool_pattern):
        return None

    # 2. args_contain 条件（ALL must match）
    for key, value in rule.get("args_contain", {}).items():
        if key not in args:
            return None
        if args.get(key) != value:
            return None

    # 3. args_match 条件（ALL must match — 字符串值 fnmatch）
    for key, pattern in rule.get("args_match", {}).items():
        val = args.get(key)
        if not isinstance(val, str):
            return None
        if not fnmatch(val, pattern):
            return None

    return rule.get("reason", "")


# ── 具体过滤器 — Phase 1 ─────────────────────────────────


class RequireConfirmFilter(PermissionFilter):
    """声明式确认过滤器 — 定义哪些 tool+args 组合需要用户确认。

    规则中的任一匹配条件命中即触发确认对话框。
    规则按注册顺序检查，返回第一个匹配的结果。
    """

    def __init__(self, rules: list[dict] | None = None) -> None:
        self._rules: list[dict] = list(rules) if rules else []

    def add_rule(self, rule: dict) -> None:
        """追加确认规则"""
        self._rules.append(rule)

    async def check(self, tool_name: str, args: dict, context: dict) -> PermissionVerdict:
        for rule in self._rules:
            reason = _match_rule(tool_name, args, rule)
            if reason:
                return PermissionVerdict(
                    allow=True,
                    require_confirm=True,
                    reason=reason,
                )
        return PermissionVerdict(allow=True)


class BlocklistFilter(PermissionFilter):
    """声明式黑名单过滤器 — 定义哪些 tool+args 组合直接拒绝。

    规则中的任一匹配条件命中即阻止执行。
    规则按注册顺序检查，返回第一个匹配的结果。
    """

    def __init__(self, rules: list[dict] | None = None) -> None:
        self._rules: list[dict] = list(rules) if rules else []

    def add_rule(self, rule: dict) -> None:
        """追加阻止规则"""
        self._rules.append(rule)

    async def check(self, tool_name: str, args: dict, context: dict) -> PermissionVerdict:
        for rule in self._rules:
            reason = _match_rule(tool_name, args, rule)
            if reason:
                return PermissionVerdict(
                    allow=False,
                    reason=reason,
                )
        return PermissionVerdict(allow=True)


# ── 具体过滤器 — Phase 2 ─────────────────────────────────


class PathScopeFilter(PermissionFilter):
    """跨工具路径穿越检测 — 检测参数中的 ../ 和绝对路径"""

    def __init__(self, workspace_path: str, block_external: bool = False) -> None:
        self._workspace = os.path.abspath(workspace_path)
        self._block_external = block_external

    async def check(self, tool_name: str, args: dict, context: dict) -> PermissionVerdict:
        for key, value in args.items():
            if not isinstance(value, str):
                continue
            # 检测路径穿越模式
            if "../" in value or "..\\" in value:
                return PermissionVerdict(
                    allow=False,
                    reason=f"参数 '{key}' 包含路径穿越模式: {value}",
                )
            # 可选：检测超出工作区的绝对路径
            if self._block_external and os.path.isabs(value):
                abs_path = os.path.abspath(value)
                if not abs_path.startswith(self._workspace):
                    return PermissionVerdict(
                        allow=False,
                        reason=f"参数 '{key}' 指向工作区外路径: {abs_path}",
                    )
        return PermissionVerdict(allow=True)


# ── 具体过滤器 — Phase 2a: 区域权限 ──────────────────────


class ZonePermissionFilter(PermissionFilter):
    """按路径区域 + 操作类型判权的统一过滤器

    区域定义：
    - writable:  .AIGEME/, character/, tachi-e/, venv/  → auto（读+写）
    - project:   PROJECT_ROOT 下除 writable 外的所有文件 → read auto, write confirm
    - system:    C:/Windows, C:/Program Files 等 → deny（所有操作）
    - external:  以上之外 → confirm
    """

    WRITABLE_DIRS = [".AIGEME", "character", "tachi-e", "venv"]
    SYSTEM_DIRS = [
        r"C:\Windows",
        r"C:\Program Files",
        r"C:\Program Files (x86)",
    ]

    WRITE_OPS = {"write", "append", "edit", "delete"}

    # 只读目录 — 即使 strict_mode=False 也禁止写入（保护运行时代码）
    READONLY_DIRS = {"core", "config", "main.py", "start.bat", "start.ps1"}

    def __init__(self, project_root: str | Path, strict_mode: bool = True) -> None:
        self._project_root = Path(project_root).resolve()
        self._strict_mode = strict_mode

    def _classify(self, path: str) -> str:
        """返回 writable / project / system / external"""
        # 纯文件名（无分隔符，非绝对路径）→ 工具会解析到 workspace，直接放行
        if path and not os.path.isabs(path) and "/" not in path and "\\" not in path:
            return "writable"

        p = Path(path).resolve()
        project_root = self._project_root

        # system: 系统关键路径
        for sys_dir in self.SYSTEM_DIRS:
            sys_path = Path(sys_dir).resolve()
            try:
                if str(p).startswith(str(sys_path)):
                    return "system"
            except OSError:
                continue

        # 不在 PROJECT_ROOT 之下 → external
        try:
            p.relative_to(project_root)
        except ValueError:
            return "external"

        # 在 PROJECT_ROOT 之下：检查是否属于 writable 区域
        for child in project_root.iterdir():
            for writable in self.WRITABLE_DIRS:
                allow_path = project_root / writable
                if allow_path.exists() and str(p.resolve()).startswith(
                    str(allow_path.resolve())
                ):
                    return "writable"

        # readonly 区域：禁止写入（保护 core/、config/ 等运行时代码）
        p_str = str(p.resolve())
        if any(p_str.startswith(str(project_root / d)) for d in self.READONLY_DIRS):
            return "readonly"

        # project root 下但不在 writable 中
        return "project"

    async def check(self, tool_name: str, args: dict, context: dict) -> PermissionVerdict:
        path = args.get("path", "")
        if not path or not isinstance(path, str):
            return PermissionVerdict(allow=True)

        zone = self._classify(path)
        op_type = "write" if args.get("operation", "") in self.WRITE_OPS else "read"

        if zone == "system":
            if self._strict_mode:
                return PermissionVerdict(allow=False, reason=f"系统路径禁止操作: {path}")
            return PermissionVerdict(allow=True, require_confirm=True, reason=f"系统路径操作需确认: {path}")
        if zone == "writable":
            return PermissionVerdict(allow=True)
        if zone == "readonly":
            if op_type == "read":
                return PermissionVerdict(allow=True)
            return PermissionVerdict(allow=False, reason=f"只读区域禁止写入: {path}")
        if zone == "project":
            if op_type == "read" or not self._strict_mode:
                return PermissionVerdict(allow=True)
            return PermissionVerdict(
                allow=True,
                require_confirm=True,
                reason=f"项目代码区域写入需确认: {path}",
            )
        # zone == "external"
        if op_type == "read" or not self._strict_mode:
            return PermissionVerdict(allow=True)
        return PermissionVerdict(
            allow=True,
            require_confirm=True,
            reason=f"外部路径写入需确认: {path}",
        )
