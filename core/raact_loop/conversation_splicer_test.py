"""Tests for conversation_splicer - history message building rules"""

from langchain_core.messages import AIMessage

from core.engine.models import RaActResponse
from core.raact_loop.conversation_splicer import build_history_message


class TestBuildHistoryMessage:
    def test_with_tool_calls_discards_reasoning(self):
        """有工具调用时，reasoning 不应保留在 history 中（思考不传给下轮）"""
        response = RaActResponse(
            reasoning="我需要查找用户信息",
            say="请稍等，我查一下",
            tool_calls=[{"name": "search_user", "arguments": {"id": "123"}}],
        )
        msg = build_history_message(response, has_tool_calls=True)
        assert isinstance(msg, AIMessage)
        assert msg.content == "请稍等，我查一下"
        assert "reasoning" not in msg.additional_kwargs or not msg.additional_kwargs.get("reasoning")

    def test_without_tool_calls_discards_reasoning(self):
        """无工具调用时，应丢弃 reasoning"""
        response = RaActResponse(
            reasoning="这个推理在无工具时不重要",
            say="好的，我知道了",
        )
        msg = build_history_message(response, has_tool_calls=False)
        assert isinstance(msg, AIMessage)
        assert msg.content == "好的，我知道了"
        assert "reasoning" not in msg.additional_kwargs

    def test_say_is_none_without_tool_calls(self):
        """say 为 None 且无工具调用时，content 应为空字符串"""
        response = RaActResponse(reasoning="思考中...", say=None)
        msg = build_history_message(response, has_tool_calls=False)
        assert msg.content == ""

    def test_say_is_none_with_tool_calls(self):
        """say 为 None 但有工具调用时，content 应为空字符串，reasoning 不保留"""
        response = RaActResponse(
            reasoning="思考中...",
            say=None,
            tool_calls=[{"name": "tool", "arguments": {}}],
        )
        msg = build_history_message(response, has_tool_calls=True)
        assert msg.content == ""
        assert "reasoning" not in msg.additional_kwargs or not msg.additional_kwargs.get("reasoning")

    def test_say_is_empty_string_without_tool_calls(self):
        """say 为空字符串且无工具调用"""
        response = RaActResponse(reasoning="思考", say="")
        msg = build_history_message(response, has_tool_calls=False)
        assert msg.content == ""

    def test_say_is_empty_string_with_tool_calls(self):
        """say 为空字符串但有工具调用，reasoning 不保留"""
        response = RaActResponse(
            reasoning="思考", say="", tool_calls=[{"name": "t", "arguments": {}}]
        )
        msg = build_history_message(response, has_tool_calls=True)
        assert msg.content == ""
        assert "reasoning" not in msg.additional_kwargs or not msg.additional_kwargs.get("reasoning")

    def test_return_type_is_aimessage(self):
        """返回值类型应为 AIMessage"""
        response = RaActResponse(say="hello")
        msg = build_history_message(response, has_tool_calls=False)
        assert isinstance(msg, AIMessage)
