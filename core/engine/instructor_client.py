"""Instructor 调用封装"""

import asyncio
import json
import re
from typing import Any, Callable
import litellm
import instructor
from instructor import AsyncInstructor
from instructor.function_calls import Mode
from langchain_core.messages import BaseMessage

# LiteLLM: 不支持的参数自动丢弃而非抛错
# 例如 reasoning_effort 对 OpenAI 兼容端点无效时降级为静默忽略
litellm.drop_params = True

from core.engine.models import RaActResponse
from core.protocols.blocks import Block


class InstructorClient:
    """Instructor 集成封装"""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        max_retries: int = 2,
        mode: Mode = instructor.Mode.JSON,
        api_base: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        preserve_thinking: bool = False,
        native_provider: bool = True,
    ) -> None:
        self._model = model
        self._mode = mode
        self._max_retries = max_retries
        self._api_base = api_base
        self._api_key = str(api_key) if api_key is not None and not isinstance(api_key, str) else api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._presence_penalty = presence_penalty
        self._frequency_penalty = frequency_penalty
        self._top_p = top_p
        self._top_k = top_k
        self.preserve_thinking = preserve_thinking  # 公开属性，供 RaActLoop 等引用
        self._native_provider = native_provider
        self._client: AsyncInstructor = instructor.from_litellm(
            litellm.acompletion,
            mode=mode,
        )

    def _get_provider(self) -> str | None:
        """从模型名中提取 provider（provider/model_name 格式）"""
        if "/" not in self._model:
            return None
        return self._model.split("/", 1)[0].strip().lower()

    @property
    def model(self) -> str:
        """获取当前模型名称"""
        return self._model

    @property
    def api_base(self) -> str | None:
        """获取 API 基础地址"""
        return self._api_base

    @property
    def api_key(self) -> str | None:
        """获取 API 密钥"""
        return self._api_key

    async def create_completion(
        self,
        messages: list[BaseMessage],
        model: str | None = None,
    ) -> RaActResponse:
        """调用 Instructor 获取结构化 RaActResponse（阻塞式，用于非流式场景）"""
        dict_messages = _messages_to_dicts(messages, preserve_thinking=self.preserve_thinking)
        model_name = model or self._model

        # 非原生 provider 时剥离 provider 前缀，避免 litellm 不认识的 provider 前缀报错
        if self._api_base and "/" in model_name and not self._native_provider:
            _, _, _name = model_name.partition("/")
            if _name:
                model_name = _name.strip()

        completion_kwargs = {}
        if self._api_base:
            completion_kwargs["api_base"] = self._api_base
            if not self._native_provider:
                completion_kwargs["custom_llm_provider"] = "openai"
        if self._api_key:
            completion_kwargs["api_key"] = self._api_key
        # 开启 thinking mode（推理模型默认，与 preserve_thinking 解耦）
        completion_kwargs["reasoning_effort"] = "high"
        extra_body: dict = {
            "chat_template_kwargs": {       # vLLM chat template
                "enable_thinking": True,
                "preserve_thinking": True,
            },
        }
        # DashScope/Qwen：通过 extra_body 传递非标准参数
        if self._get_provider() == "dashscope":
            extra_body["enable_thinking"] = True
        # 智谱 BigModel：thinking.type + clear_thinking
        if self._get_provider() == "bigmodel":
            extra_body["thinking"] = {
                "type": "enabled",
                "clear_thinking": not self.preserve_thinking,
            }
        completion_kwargs["extra_body"] = extra_body

        response: RaActResponse = await self._client.chat.completions.create(
            model=model_name,
            response_model=RaActResponse,
            messages=dict_messages,
            max_retries=self._max_retries,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            top_p=self._top_p,
            **completion_kwargs,
        )
        return response

    async def create_completion_stream(
        self,
        messages: list,
        send_block: Callable,
        model: str | None = None,
        tools: list[dict] | None = None,
        cancelled_check: Callable[[], bool] | None = None,
        tool_choice: str | None = None,
    ) -> RaActResponse:
        """流式调用 LLM，推送 thinking/speech 到前端，流结束后提取 tool_calls

        流程:
          1. litellm streaming，实时推 thinking/speech block
          2. 用 index 跟踪收集流式 tool_calls chunks
          3. 流结束后检查 finish_reason，如果 == "tool_calls" 则使用收集的数据
          4. 构建 RaActResponse 返回

        Args:
            tool_choice: 控制工具调用行为，"auto"/"none"/"required"/None(默认litellm行为)
        """
        dict_messages = _messages_to_dicts(messages, preserve_thinking=self.preserve_thinking)
        model_name = model or self._model

        # 当 api_base 显式设置时：
        # - native_provider=True（如 OpenAI、DeepSeek、BigModel 等）
        #   → 保留 provider 前缀，让 litellm 走内置 provider 路由
        # - native_provider=False（自定义端点、Ollama/vLLM 等）
        #   → 剥离 provider 前缀，用 custom_llm_provider="openai"
        if self._api_base and "/" in model_name:
            _provider, _, _name = model_name.partition("/")
            if _name:
                if self._native_provider:
                    # 保留 provider/model_name 格式，走 litellm 原生路由
                    model_name = f"{_provider}/{_name}"
                else:
                    model_name = _name.strip()

        full_content = ""
        reasoning_content = ""
        expression_found = False       # 标记是否已提取表情标签
        clean_content = ""             # 不含标签的纯净文本
        finish_reason = None
        _tag_depth = 0                 # 标签嵌套深度：0=正常，1=在 <tachie-e> 括号内或表达式名中
        _tag_depth_safe_counter = 0    # 防止状态机死锁
        MAX_TAG_DEPTH_CHARS = 100      # 安全上限

        # 流式 tool_calls 收集（按 index 跟踪跨 chunk 同名工具）
        partial_tool_calls: dict[int, dict] = {}

        # 构建 OpenAI 格式的 tools 参数（含通用缓存标记）
        openai_tools = None
        if tools:
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    },
                    "cache_control": {"type": "ephemeral"},
                }
                for t in tools
            ]

        kwargs = {}
        if self._api_base:
            kwargs["api_base"] = self._api_base
            if not self._native_provider:
                kwargs["custom_llm_provider"] = "openai"  # 非原生 → 强制走 OpenAI 兼容协议
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._presence_penalty is not None:
            kwargs["presence_penalty"] = self._presence_penalty
        if self._frequency_penalty is not None:
            kwargs["frequency_penalty"] = self._frequency_penalty
        if self._top_p is not None:
            kwargs["top_p"] = self._top_p
        if self._top_k is not None:
            kwargs["top_k"] = self._top_k
        # 开启 thinking mode（推理模型默认行为，与 preserve_thinking 解耦）
        # preserve_thinking 仅控制 reasoning_content 是否回传给下一轮
        kwargs["reasoning_effort"] = "high"
        extra_body: dict = {
            "chat_template_kwargs": {       # vLLM chat template
                "enable_thinking": True,
                "preserve_thinking": True,
            },
        }
        # DashScope/Qwen：通过 extra_body 传递非标准参数
        if self._get_provider() == "dashscope":
            extra_body["enable_thinking"] = True
        # 智谱 BigModel：thinking.type + clear_thinking
        if self._get_provider() == "bigmodel":
            extra_body["thinking"] = {
                "type": "enabled",
                "clear_thinking": not self.preserve_thinking,
            }
        kwargs["extra_body"] = extra_body

        stream = await litellm.acompletion(
            model=model_name,
            messages=dict_messages,
            stream=True,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            tools=openai_tools,
            tool_choice=tool_choice or "auto",
            **kwargs,
        )

        async for chunk in stream:
            # 检查取消状态 — 不抛 CancelledError，只关闭流并退出循环
            # 保持 HTTP 请求正常完成（而非连接断开），让 vLLM 保留 KV 缓存供后续请求复用
            if cancelled_check and cancelled_check():
                await stream.aclose()
                break

            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue

            # 记录 finish_reason
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            delta = choice.delta

            # thinking（reasoning_content）
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning_content += rc
                await send_block(Block(
                    block_type="thinking", delta=rc, is_final=False,
                ))
                continue

            # content（say）
            c = delta.content or ""
            if c:
                full_content += c

                # 逐字符追踪 <tachie-e> 标签（流式跨 chunk 状态机）
                # 状态：_tag_depth == 0 时正常输出，> 0 时在标签内（抑制输出）
                # 进入 <tachie-e 的 <> 时 depth+1，遇到 > 时 depth+1（进入表达式名区域）
                # 遇到 </tachie-e 的 < 时 depth+1，> 时 depth-1 回到 0
                speech_buffer = ""
                for ch in c:
                    if _tag_depth == 0 and ch == '<':
                        _tag_depth = 1          # 进入开标签的 <>
                        _tag_depth_safe_counter = 0
                    elif _tag_depth == 1 and ch == '>':
                        _tag_depth = 2          # 开标签结束，进入表达式名区域
                        _tag_depth_safe_counter = 0
                    elif _tag_depth == 1 and ch == '<':
                        _tag_depth = 1          # 连续 <<，仍在内
                        _tag_depth_safe_counter = 0
                    elif _tag_depth == 2 and ch == '<':
                        _tag_depth = 3          # 进入闭标签的 <>
                        _tag_depth_safe_counter = 0
                    elif _tag_depth == 2 and ch == '>':
                        _tag_depth = 0          # 孤立的 > 视为异常，回正常
                        _tag_depth_safe_counter = 0
                    elif _tag_depth == 3 and ch == '>':
                        _tag_depth = 0          # 闭标签结束，回到正常
                        _tag_depth_safe_counter = 0
                    elif _tag_depth > 0:
                        _tag_depth_safe_counter += 1
                        # 安全检测：标签内停留超过上限字符数，强制退出
                        if _tag_depth_safe_counter >= MAX_TAG_DEPTH_CHARS:
                            _tag_depth = 0
                            _tag_depth_safe_counter = 0
                            speech_buffer += ch  # 恢复截断后的文本
                            continue
                        pass                    # 标签内字符，丢弃
                    else:
                        speech_buffer += ch     # 非标签字符，保留

                # 实时检测完整的 <tachie-e>表情名</tachie-e> 标签
                if not expression_found:
                    tag_match = re.search(r"<tachie-e>(\w+)</tachie-e>", full_content)
                    if tag_match:
                        expression_found = True
                        expression_name = tag_match.group(1)
                        clean_content = re.sub(r"<tachie-e>\w+</tachie-e>", "", full_content).strip()
                        await send_block(Block(
                            block_type="expression", delta=expression_name, is_final=True,
                        ))

                # 推送纯净的 speech（不含任何标签文字）
                if speech_buffer:
                    await send_block(Block(
                        block_type="speech", delta=speech_buffer, is_final=False,
                    ))

            # 收集 tool_calls chunks（用 index 跟踪）
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in partial_tool_calls:
                        partial_tool_calls[idx] = {"name": "", "arguments_str": ""}
                    if tc.id:
                        partial_tool_calls[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            partial_tool_calls[idx]["name"] += tc.function.name
                        if tc.function.arguments:
                            partial_tool_calls[idx]["arguments_str"] += tc.function.arguments

        # 最终标记
        if reasoning_content:
            await send_block(Block(block_type="thinking", delta="", is_final=True))
        if full_content:
            # 最终 speech 剥离所有标签残片（包括流式截断导致的未闭合标签）
            final_speech = clean_content if expression_found else re.sub(
                r"<tachie-e>[^>]*>?|</tachie-e>", "", full_content
            ).strip()
            await send_block(Block(block_type="speech", delta=final_speech, is_final=True))

        # 流结束后：根据 finish_reason 和收集的数据构建 RaActResponse
        from core.engine.models import ToolCallDef

        tool_defs: list[ToolCallDef] | None = None

        if finish_reason == "tool_calls" and partial_tool_calls:
            tool_defs = []
            for idx in sorted(partial_tool_calls.keys()):
                tc = partial_tool_calls[idx]
                try:
                    args = json.loads(tc.get("arguments_str", "{}"))
                except json.JSONDecodeError:
                    args = {}
                tool_defs.append(ToolCallDef(
                    id=tc.get("id", ""),         # 保留 LLM 返回的原始 ID
                    name=tc.get("name", ""),
                    arguments=args,
                ))

        return RaActResponse(
            reasoning=reasoning_content,
            say=(clean_content if expression_found else re.sub(
                r"<tachie-e>[^>]*>?|</tachie-e>", "", full_content
            ).strip()) or None,
            tool_calls=tool_defs,
        )


def _messages_to_dicts(messages: list, preserve_thinking: bool = False) -> list[dict]:
    """将消息列表转换为字典列表（适配 OpenAI/DeepSeek 格式）
    支持 LangChain BaseMessage 对象或普通 dict"""
    result = []
    for msg in messages:
        # 如果已经是 dict，直接使用
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            d = dict(msg)  # 复制一份
            # 确保 role 和 content 存在
            d.setdefault("content", "")
            result.append(d)
            continue

        # BaseMessage 对象
        role = _role_of(msg)
        d: dict = {
            "role": role,
            "content": msg.content or "",
        }
        # tool_calls 从 additional_kwargs 提升到顶层
        if hasattr(msg, "additional_kwargs") and msg.additional_kwargs:
            if "tool_calls" in msg.additional_kwargs:
                d["tool_calls"] = msg.additional_kwargs["tool_calls"]
            else:
                d["additional_kwargs"] = msg.additional_kwargs
            # preserve_thinking: 传递 reasoning_content 给 LLM 模板
            if preserve_thinking and "reasoning_content" in msg.additional_kwargs:
                d["reasoning_content"] = msg.additional_kwargs["reasoning_content"]
        # tool 消息保留 tool_call_id
        if role == "tool" and hasattr(msg, "tool_call_id") and msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        result.append(d)
    return result


def _role_of(msg: BaseMessage) -> str:
    """获取消息角色名"""
    role = msg.__class__.__name__
    if role == "HumanMessage":
        return "user"
    if role == "AIMessage":
        return "assistant"
    if role == "ToolMessage":
        return "tool"
    if role == "SystemMessage":
        return "system"
    return "user"
