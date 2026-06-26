"""StreamRouter — 将 RaActResponse 拆解为多个 Block 流式分发"""

import re

from core.protocols.blocks import Block


def parse_expression_tag(text: str) -> tuple[str, str | None]:
    """
    从文本中提取 <tachie-e>表情名</tachie-e> 标签。

    返回: (纯净文本, 表情名或 None)
    """
    match = re.search(r"<tachie-e>(\w+)</tachie-e>", text)
    if not match:
        return text, None
    expression = match.group(1)
    clean_text = text.replace(match.group(0), "").strip()
    return clean_text, expression


def route_response(
    reasoning: str,
    say: str | None,
    has_tool_calls: bool = False,
) -> list[Block]:
    """
    将 RaActResponse 的字段拆解为 Block 列表。

    返回一个有序的 Block 列表，按: thinking → speech → expression 顺序。
    注意: tool_call 和 tool_result 由调用者在工具执行后单独推送。
    """
    blocks: list[Block] = []

    # 1. reasoning → thinking block
    if reasoning:
        blocks.append(
            Block(
                block_type="thinking",
                delta=reasoning,
                is_final=True,
            )
        )

    # 2. say → speech + expression
    if say:
        clean_say, expression = parse_expression_tag(say)

        if expression:
            blocks.append(
                Block(
                    block_type="expression",
                    delta=expression,
                    is_final=True,
                )
            )

        # 即使 clean_say 为空（纯标签消息）也发送 speech block
        blocks.append(
            Block(
                block_type="speech",
                delta=clean_say or "",
                is_final=True,
            )
        )

    return blocks
