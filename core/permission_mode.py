"""Permission Mode — 权限模式枚举"""

from enum import Enum


class PermissionMode(str, Enum):
    """权限模式

    FULL_AUTO:  全部放行（本地可信 LLM 场景）
    NORMAL:     默认模式，拦截 core/.git 写入，脚本内联执行需确认
    RESTRICTED: bash 不可用
    """
    FULL_AUTO = "full_auto"
    NORMAL = "normal"
    RESTRICTED = "restricted"
