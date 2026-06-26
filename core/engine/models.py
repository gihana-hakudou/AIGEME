"""RaActResponse + ToolCallDef Pydantic 模型"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolCallDef(BaseModel):
    """工具调用定义"""

    name: str = Field(description="工具名称，必须匹配已注册的工具名")
    arguments: dict[str, Any] = Field(description="工具参数字典")
    id: str = Field(default="", description="LLM 返回的原始工具调用 ID")


class RaActResponse(BaseModel):
    """LLM 单次调用输出，由 Instructor 保障 Pydantic 格式"""

    reasoning: str = Field(
        default="",
        description="角色的内心思考/推理过程，显示在思考面板。可为空字符串。",
    )
    say: Optional[str] = Field(
        default=None,
        description="对用户说的话。留空或 null 则不输出对话文本。"
        "可在文本末尾附加 <tachie-e>表情名</tachie-e> 标签控制立绘表情。",
    )
    tool_calls: Optional[list[ToolCallDef]] = Field(
        default=None,
        description="需要调用的工具列表。null 或空列表表示本轮结束，不再继续循环。",
    )
