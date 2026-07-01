"""Browser Control — Chrome CDP 浏览器控制模块

browser_execute 工具通过 Python 代码操作浏览器（多步骤组合），
也可通过 bash CLI `python -m core.tools.browser.cli` 执行单步操作。
"""

from .tools import (
    BrowserExecuteTool,
)

__all__ = [
    "BrowserExecuteTool",  # 工具已注册供 LLM 调用，也可通过 CLI: python -m core.tools.browser.cli
]


def register_all(registry):
    """批量注册所有浏览器工具到工具注册表"""
    registry.register(BrowserExecuteTool())
