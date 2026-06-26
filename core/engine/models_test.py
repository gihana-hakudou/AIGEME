"""Tests for RaActResponse and ToolCallDef models"""

import json

import pytest
from pydantic import ValidationError

from core.engine.models import RaActResponse, ToolCallDef


class TestToolCallDef:
    def test_minimal_tool_call(self):
        """最简 ToolCallDef"""
        tc = ToolCallDef(name="test_tool", arguments={"key": "value"})
        assert tc.name == "test_tool"
        assert tc.arguments == {"key": "value"}

    def test_empty_arguments(self):
        """允许空参数字典"""
        tc = ToolCallDef(name="no_args", arguments={})
        assert tc.arguments == {}

    def test_missing_name(self):
        """缺少 name 字段应报错"""
        with pytest.raises(ValidationError):
            ToolCallDef(arguments={})  # type: ignore

    def test_missing_arguments(self):
        """缺少 arguments 字段应报错"""
        with pytest.raises(ValidationError):
            ToolCallDef(name="test")  # type: ignore

    def test_complex_arguments(self):
        """支持嵌套结构的 arguments"""
        tc = ToolCallDef(
            name="complex_tool",
            arguments={
                "nested": {"deep": {"value": 42}},
                "list": [1, 2, 3],
            },
        )
        assert tc.arguments["nested"]["deep"]["value"] == 42
        assert tc.arguments["list"] == [1, 2, 3]

    def test_serialization_roundtrip(self):
        """ToolCallDef JSON 序列化/反序列化往返"""
        tc = ToolCallDef(name="roundtrip", arguments={"x": 1})
        json_str = tc.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["name"] == "roundtrip"
        assert parsed["arguments"] == {"x": 1}
        restored = ToolCallDef(**parsed)
        assert restored == tc


class TestRaActResponse:
    def test_empty_response(self):
        """最简 RaActResponse（全部默认值）"""
        resp = RaActResponse()
        assert resp.reasoning == ""
        assert resp.say is None
        assert resp.tool_calls is None

    def test_reasoning_only(self):
        """仅提供 reasoning"""
        resp = RaActResponse(reasoning="思考中...")
        assert resp.reasoning == "思考中..."
        assert resp.say is None
        assert resp.tool_calls is None

    def test_say_only(self):
        """仅提供 say 文本"""
        resp = RaActResponse(say="你好，世界！")
        assert resp.reasoning == ""
        assert resp.say == "你好，世界！"
        assert resp.tool_calls is None

    def test_say_with_tachie_tag(self):
        """say 文本包含立绘标签"""
        resp = RaActResponse(say="我好开心！<tachie-e>happy</tachie-e>")
        assert "<tachie-e>happy</tachie-e>" in resp.say

    def test_with_tool_calls(self):
        """带 tool_calls 的响应"""
        resp = RaActResponse(
            reasoning="我需要查资料",
            say="让我查一下",
            tool_calls=[
                ToolCallDef(name="search", arguments={"q": "weather"}),
            ],
        )
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "search"
        assert resp.tool_calls[0].arguments == {"q": "weather"}

    def test_empty_tool_calls_list(self):
        """tool_calls 可以是空列表"""
        resp = RaActResponse(tool_calls=[])
        assert resp.tool_calls == []

    def test_say_can_be_null(self):
        """say 字段允许为 None"""
        resp = RaActResponse(say=None)
        assert resp.say is None

    def test_say_can_be_empty_string(self):
        """say 字段允许为空字符串"""
        resp = RaActResponse(say="")
        assert resp.say == ""

    def test_serialization_roundtrip(self):
        """RaActResponse JSON 序列化/反序列化往返"""
        resp = RaActResponse(
            reasoning="推理过程",
            say="对话文本",
            tool_calls=[
                ToolCallDef(name="tool1", arguments={"a": 1}),
            ],
        )
        json_str = resp.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["reasoning"] == "推理过程"
        assert parsed["say"] == "对话文本"
        assert len(parsed["tool_calls"]) == 1

        restored = RaActResponse(**parsed)
        assert restored.reasoning == resp.reasoning
        assert restored.say == resp.say
        assert restored.tool_calls is not None
        assert restored.tool_calls[0].name == "tool1"

    def test_partial_deserialization(self):
        """从部分 dict 反序列化应使用默认值"""
        data = {"say": "hello"}
        resp = RaActResponse(**data)
        assert resp.say == "hello"
        assert resp.reasoning == ""
        assert resp.tool_calls is None

    def test_multiple_tool_calls(self):
        """支持多个工具调用"""
        resp = RaActResponse(
            tool_calls=[
                ToolCallDef(name="tool_a", arguments={"x": 1}),
                ToolCallDef(name="tool_b", arguments={"y": 2}),
            ],
        )
        assert len(resp.tool_calls) == 2
