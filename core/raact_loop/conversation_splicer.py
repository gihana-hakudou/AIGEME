"""对话拼接 — 有工具保留 reasoning，无工具丢弃"""

import json

from langchain_core.messages import AIMessage

from core.engine.models import RaActResponse


def build_history_message(
    raact_response: RaActResponse,
    has_tool_calls: bool,
) -> AIMessage:
    """
    拼接消息到对话历史 (LangChain 格式)。

    仅保留 say 文本和 tool_calls，不保留 reasoning（思考过程不传给下轮）。

    有工具调用:
      - 保留 say + tool_calls（通过 additional_kwargs 传递），
        确保后续 LLM 调用时 tool 消息有对应的 assistant tool_calls 前缀

    无工具调用:
      - 仅保留 say
    """
    if has_tool_calls:
        # 构造 tool_calls 的 OpenAI 格式（与 _build_assistant_message 一致）
        tool_calls_data = []
        if raact_response.tool_calls:
            tool_calls_data = [
                {
                    "id": tc.id or f"call_history_{i}",
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for i, tc in enumerate(raact_response.tool_calls)
            ]
        return AIMessage(
            content=raact_response.say or "",
            additional_kwargs={
                "reasoning": "",
                "tool_calls": tool_calls_data,
            },
        )
    return AIMessage(content=raact_response.say or "")
