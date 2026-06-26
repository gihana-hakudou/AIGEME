"""BaseTool 抽象基类 + 输出类型预定义"""

from abc import ABC, abstractmethod
from typing import Any, Literal

# ── 预定义输出类型（新工具注册时选填一个）───────────────
# text         : 纯文本字符串（document write/delete/memory操作）
# json         : 任意可序列化的 dict/list（system env、通用 json）
# bash         : {"stdout": str, "stderr": str, "returncode": int}
# file_read    : {"file":str, "total_lines":int, "content":str, ...}
# file_list    : {"path": str, "files": [{"name","type","size"}]}
# file_search  : {"file":str, "query":str, "match_count":int, "matches":[...]}
# skill_search : {"count": int, "results": [{"name","description"}]}
# skill_content: {"name": str, "content": str}
# image        : {"file":str, "width":int, "height":int, "data_url":str, ...}
#                → RaAct 循环自动将 data_url 注入为多模态 user 消息供 LLM 分析
# ──────────────────────────────────────────────────────
ToolOutputType = Literal[
    "text", "json", "bash", "file_read",
    "file_list", "file_search", "skill_search", "skill_content",
    "image",
]


class BaseTool(ABC):
    """工具基类：所有工具继承此类"""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    output_type: ToolOutputType = "json"  # ← 默认为 json，新增工具须按实际声明

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """执行工具，返回结果"""
