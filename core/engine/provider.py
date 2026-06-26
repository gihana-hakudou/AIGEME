"""LiteLLM 封装 — Provider 策略 + Instructor client 初始化"""

from langchain_litellm import ChatLiteLLM


def create_llm(
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    **kwargs: object,
) -> ChatLiteLLM:
    """创建 ChatLiteLLM 实例"""
    return ChatLiteLLM(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,  # type: ignore[arg-type]
    )
