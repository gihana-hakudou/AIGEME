"""ContextCompressor — 上下文压缩器

正确行为：
1. 从 config 读取 context_window + token_limit_ratio 作为触发阈值
2. 不再有 PROTECT_COUNT / PROTECT_TURNS 常量
3. 触发后调用 trim_tools() 保持最近 10 轮完整
4. 精确 token 估算：优先调用 LLM 后端的 /tokenize 接口，不可用时回退到启发式
"""

import json
import logging
from typing import Any
from urllib.request import Request, urlopen

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 2.0  # 中英文混合启发式回退值
MESSAGE_OVERHEAD = 80  # 每条消息的 API 格式开销
DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_TOKEN_LIMIT_RATIO = 0.9
DEFAULT_KEEP_TOOL_TURNS = 5
DEFAULT_TRUNCATE_TOOL_CONTENT_LENGTH = 500  # 旧轮次工具返回内容截断长度

# 缓存 tokenize 接口的可用性，避免每次调用都尝试
_tokenize_available: bool | None = None
_tokenize_api_base: str | None = None
_tiktoken_available: bool | None = None
_tiktoken_model: str | None = None
_tiktoken_enc: Any = None


async def _try_tokenize(text: str) -> int | None:
    """尝试调用 LLM 后端的 /tokenize 接口获取精确 token 数。

    适用于 llama.cpp 等本地推理后端（自带 /tokenize 端点）。
    返回 token 数，接口不可用或出错时返回 None。
    结果会被缓存，避免重复尝试。
    """
    global _tokenize_available, _tokenize_api_base

    if _tokenize_available is False:
        return None

    if _tokenize_api_base is None:
        try:
            from core.config.settings import get_config
            config = get_config()
            _tokenize_api_base = config.get("llm", {}).get("api_base", "").rstrip("/")
        except Exception:
            _tokenize_available = False
            return None

    if not _tokenize_api_base:
        _tokenize_available = False
        return None

    try:
        url = f"{_tokenize_api_base}/tokenize"
        data = json.dumps({"content": text}).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        import asyncio
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: urlopen(req, timeout=2))
        body = json.loads(resp.read().decode("utf-8"))
        tokens = body.get("tokens") or body.get("content", [])
        count = len(tokens) if isinstance(tokens, list) else 0
        if count > 0:
            _tokenize_available = True
            return count
        _tokenize_available = False
        return None
    except Exception as e:
        logger.debug("tokenize 接口不可用（非 llama.cpp 后端？），尝试 tiktoken: %s", e)
        _tokenize_available = False
        return None


def _try_tiktoken(text: str) -> int | None:
    """使用 tiktoken 根据模型名估算 token 数。

    适用于 OpenAI / DeepSeek 等 API 后端。
    返回 token 数，不可用时返回 None。
    结果会被缓存，避免重复尝试。
    """
    global _tiktoken_available, _tiktoken_model, _tiktoken_enc

    if _tiktoken_available is False:
        return None

    if _tiktoken_enc is None:
        try:
            import tiktoken
        except ImportError:
            _tiktoken_available = False
            return None

        # 首次调用，从 config 读取 model 名选择编码
        if _tiktoken_model is None:
            try:
                from core.config.settings import get_config
                config = get_config()
                _tiktoken_model = config.get("llm", {}).get("model", "")
            except Exception:
                _tiktoken_available = False
                return None

        model_lower = _tiktoken_model.lower()

        # 模型名 → tiktoken 编码映射
        if "gpt-4o" in model_lower or "gpt4o" in model_lower:
            enc_name = "o200k_base"
        elif "gpt-4" in model_lower:
            enc_name = "cl100k_base"
        elif "gpt-3.5" in model_lower:
            enc_name = "cl100k_base"
        elif "deepseek" in model_lower:
            # DeepSeek 使用自己的 tokenizer，tiktoken 不直接支持
            # cl100k_base 是最接近的近似（中英文混合误差约 10-20%）
            enc_name = "cl100k_base"
        elif "qwen" in model_lower:
            # Qwen 使用自己的 tokenizer，tiktoken 不直接支持
            # 用 cl100k_base 或 o200k_base 近似
            enc_name = "o200k_base"
        elif "llama" in model_lower:
            enc_name = "cl100k_base"
        elif "claude" in model_lower:
            enc_name = "cl100k_base"  # Anthropic 有自己的 tokenizer，近似
        elif "gemini" in model_lower:
            enc_name = "cl100k_base"  # Google 有自己的 tokenizer，近似
        else:
            # 未知模型，用 o200k_base 作为通用近似
            enc_name = "o200k_base"

        try:
            _tiktoken_enc = tiktoken.get_encoding(enc_name)
        except Exception:
            _tiktoken_available = False
            return None

    try:
        tokens = _tiktoken_enc.encode(text)
        _tiktoken_available = True
        return len(tokens)
    except Exception as e:
        logger.debug("tiktoken 估算失败，回退到启发式: %s", e)
        _tiktoken_available = False
        return None


def _format_messages_for_tokenize(messages: list[Any]) -> str:
    """将 BaseMessage 列表格式化为近似 LLM API 调用格式的纯文本。

    目的是让 tokenize 接口估算出的 token 数尽量接近实际 API 调用时的消耗。
    格式为: <|im_start|>role\ncontent<|im_end|> (近似 ChatML)
    """
    parts = []
    for msg in messages:
        role = type(msg).__name__.replace("Message", "").lower()
        if role == "human":
            role = "user"
        elif role == "ai":
            role = "assistant"
        content = msg.content or ""
        parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
    return "\n".join(parts)


def estimate_tokens(messages: list[Any]) -> int:
    """启发式估算 Token 数（同步，向后兼容）。
    
    优先使用 async estimate_tokens_async 获得精确计数。
    """
    total_chars = 0
    for msg in messages:
        total_chars += MESSAGE_OVERHEAD
        content = msg.content or ""
        total_chars += len(str(content))
        if isinstance(msg, AIMessage) and msg.additional_kwargs:
            for val in msg.additional_kwargs.values():
                total_chars += len(str(val))
        if isinstance(msg, ToolMessage):
            total_chars += 100
    return int(total_chars / CHARS_PER_TOKEN)


async def estimate_tokens_async(messages: list[Any]) -> int:
    """精确估算 Token 数（异步）。
    
    策略（优先级从高到低）：
    1. llama.cpp /tokenize 接口（最精确，适用于本地推理）
    2. tiktoken 模型编码（适用于 OpenAI / DeepSeek 等 API）
    3. 启发式 chars/2（通用回退）
    """
    text = _format_messages_for_tokenize(messages)

    # 策略 1: /tokenize 接口（llama.cpp）
    precise = await _try_tokenize(text)
    if precise is not None:
        return precise

    # 策略 2: tiktoken（OpenAI / DeepSeek / 等）
    tiktok = _try_tiktoken(text)
    if tiktok is not None:
        return tiktok

    # 策略 3: 启发式回退
    return estimate_tokens(messages)


def format_messages_to_text(messages: list[Any]) -> str:
    """将消息列表格式化为纯文本（供 LLM 深度压缩使用）"""
    lines = []
    for i, msg in enumerate(messages):
        role = type(msg).__name__.replace("Message", "").lower()
        content = msg.content or ""
        lines.append(f"[{i}] {role}: {content[:200]}")
    return "\n".join(lines)


def trim_tools(
    history: list[Any],
    keep_turns: int = DEFAULT_KEEP_TOOL_TURNS,
    truncate_length: int = DEFAULT_TRUNCATE_TOOL_CONTENT_LENGTH,
) -> list[Any]:
    """从历史中清理超过 keep_turns 轮的 tools 信息（调用 + 返回）。

    以 HumanMessage 为轮次边界。
    保留最后 keep_turns 轮的完整内容（含 tool_calls + ToolMessage）。
    对更旧的轮次：截断 ToolMessage 内容到 truncate_length 字符，保留 AIMessage 的 tool_calls。
    """
    if not history:
        return []

    # 找到所有 HumanMessage 的位置
    user_indices = [i for i, msg in enumerate(history) if isinstance(msg, HumanMessage)]

    if len(user_indices) <= keep_turns:
        # 不足 keep_turns 轮，全部保留
        return list(history)

    # keep_turns <= 0 时，全部清理（注意 Python 中 list[-0] == list[0] 的特殊性）
    if keep_turns <= 0:
        cutoff = len(history)
    else:
        # 从末尾往前数，找到第 keep_turns 个 user 消息的位置
        cutoff = user_indices[-keep_turns]

    # 保护区间：cutoff 之后的所有消息完整保留
    protected = history[cutoff:]
    to_clean = history[:cutoff]

    # 清理旧轮次：截断 ToolMessage 内容，保留 AIMessage 的 tool_calls
    cleaned = []
    for msg in to_clean:
        if isinstance(msg, ToolMessage):
            # 保留工具信息，但截断内容
            truncated_content = msg.content[:truncate_length]
            if len(msg.content) > truncate_length:
                truncated_content += f"\n...(截断，剩余 {len(msg.content) - truncate_length} 字符未显示)"
            new_msg = ToolMessage(
                content=truncated_content,
                tool_call_id=msg.tool_call_id,
                name=msg.name if hasattr(msg, 'name') and msg.name else None,
            )
            # 保留其他 kwargs
            if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
                new_msg.additional_kwargs.update(msg.additional_kwargs)
            cleaned.append(new_msg)
            continue
        if isinstance(msg, AIMessage):
            # 旧轮次：保留 tool_calls，但清除 reasoning_content 和 reasoning 节省 Token
            new_msg = AIMessage(content=msg.content)
            if msg.additional_kwargs.get("tool_calls"):
                new_msg.additional_kwargs["tool_calls"] = msg.additional_kwargs["tool_calls"]
            cleaned.append(new_msg)
            continue
        cleaned.append(msg)

    return cleaned + protected


class ContextCompressor:
    """上下文压缩器"""

    def __init__(
        self,
        keep_tool_turns: int = DEFAULT_KEEP_TOOL_TURNS,
        token_limit_ratio: float = DEFAULT_TOKEN_LIMIT_RATIO,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        instructor=None,
        truncate_length: int = DEFAULT_TRUNCATE_TOOL_CONTENT_LENGTH,
    ) -> None:
        self._keep_tool_turns = keep_tool_turns
        self._token_limit_ratio = token_limit_ratio
        self._context_window = context_window
        self._instructor = instructor
        self._truncate_length = truncate_length

    async def _deep_compress(self, history: list[Any]) -> str | None:
        """深度压缩：让 LLM 总结旧对话历史为摘要

        当 trim_tools 压缩后 token 仍然超限时调用。
        将需要压缩的旧轮次格式化为纯文本，调用 LLM 生成摘要。

        Args:
            history: 需要压缩的旧对话（不含 protected 区间）

        Returns:
            压缩提示字符串（注入为 user 消息），失败时返回 None
        """
        if not self._instructor or not history:
            return None

        text = format_messages_to_text(history)
        # 截断防止摘要调用本身超限
        if len(text) > 10000:
            text = text[:10000] + "\n...(截断)"

        system_prompt = (
            "你是对话摘要助手。请将以下 AI 与用户的对话内容总结为一段简洁的摘要（200字以内），"
            "保留关键信息、用户偏好和重要决策。只输出摘要，不要其他内容。"
        )

        try:
            response = await self._instructor.create_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请总结以下对话：\n\n{text}"},
                ],
            )
            summary = response.say or ""
            if summary.strip():
                logger.info("深度压缩完成: 生成了 %d 字符摘要", len(summary))
                return (
                    "## 对话历史摘要\n\n"
                    "以下是对之前对话的压缩摘要，请基于此继续当前对话：\n\n"
                    f"{summary}"
                )
        except Exception as e:
            logger.warning("深度压缩失败: %s", e)

        return None

    async def check_and_compress(
        self,
        history: list[Any],
        got_error: bool = False,
    ) -> tuple[list[Any], str | None]:
        """检查并执行上下文压缩。

        行为：如果 token 超过阈值，调用 trim_tools() 清理旧轮次 tools。
        """
        if not history:
            return history, None

        # 计算当前 Token 数（优先精确接口）
        token_count = await estimate_tokens_async(history)
        threshold = int(self._context_window * self._token_limit_ratio)

        # 判断是否触发
        if not got_error and token_count < threshold:
            logger.debug(
                "上下文无需压缩: %d tokens < %d (%.0f%%)",
                token_count, threshold, token_count / self._context_window * 100,
            )
            return history, None

        logger.info(
            "触发上下文压缩: %d tokens / %d (%.0f%%) 窗口 %d, 保留最近 %d 轮tools",
            token_count, threshold, token_count / self._context_window * 100,
            self._context_window, self._keep_tool_turns,
        )

        # 执行 trim_tools
        compressed = trim_tools(history, self._keep_tool_turns, self._truncate_length)

        new_tokens = await estimate_tokens_async(compressed)
        logger.info(
            "压缩完成: %d tokens → %d tokens (-%.0f%%)",
            token_count, new_tokens,
            (token_count - new_tokens) / token_count * 100 if token_count > 0 else 0,
        )

        # 检查压缩后是否仍超限
        if new_tokens >= threshold:
            logger.info(
                "trim_tools 后仍超限 (%d >= %d)，触发深度压缩",
                new_tokens, threshold,
            )
            # 找到 protected 区间之外的部分进行总结
            user_indices = [i for i, msg in enumerate(history) if isinstance(msg, HumanMessage)]
            if len(user_indices) > self._keep_tool_turns:
                cutoff = user_indices[-self._keep_tool_turns]
                to_summarize = history[:cutoff]
                compression_prompt = await self._deep_compress(to_summarize)
                if compression_prompt:
                    # 深度压缩成功：旧轮次被摘要替代，只保留保护轮次
                    protected = history[cutoff:]
                    logger.info(
                        "深度压缩: 用摘要替代 %d 条旧历史，保留 %d 条保护轮次",
                        len(to_summarize), len(protected),
                    )
                    return protected, compression_prompt
                logger.info("深度压缩失败，降级为更激进的 trim")
                # 降级：进一步缩小 keep_turns
                compressed = trim_tools(history, max(1, self._keep_tool_turns // 2), self._truncate_length)
                return compressed, None

        return compressed, None
