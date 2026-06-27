"""Permission Framework — 精简版：仅保留 PermissionVerdict 数据类

旧版 PermissionFilter / PermissionChain / 各类过滤器已移除，
所有安全检查已统一到 bash_tools.py 的 _check_command_risk 中。
"""

from dataclasses import dataclass


@dataclass
class PermissionVerdict:
    """权限判定结果"""

    action: str = ""
    allow: bool = True
    reason: str = ""
    require_confirm: bool = False
