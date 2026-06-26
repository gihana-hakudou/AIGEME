"""Browser Control — Chrome CDP 浏览器控制模块（CLI 入口）

所有浏览器操作通过 bash CLI `python -m core.tools.browser.cli` 执行。
LLM 不再使用 `browser_execute(code=...)` 工具调用，而是通过 bash 工具
运行 CLI 命令或 script 模式（多步骤写入 .py 文件批量运行）。

保留 `register_all()` 实现向后兼容，但推荐使用 CLI 方式。
"""

from .tools import (
    BrowserExecuteTool,
    BrowserSearchTool,
    BrowserExtractTool,
)

__all__ = [
    "BrowserExecuteTool",  # deprecated — 请使用 CLI: python -m core.tools.browser.cli
    "BrowserSearchTool",   # deprecated
    "BrowserExtractTool",  # deprecated
]


def register_all(registry):
    """批量注册所有浏览器工具到工具注册表"""
    registry.register(BrowserExecuteTool())
    registry.register(BrowserSearchTool())
    registry.register(BrowserExtractTool())
