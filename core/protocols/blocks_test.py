"""Tests for Block protocol models - serialization/deserialization"""

import json
from typing import get_args

import pytest
from pydantic import ValidationError

from core.protocols.blocks import Block, BlockType, ClientMessage


class TestBlockType:
    def test_all_block_types_defined(self):
        """验证所有 16 种 BlockType 值都存在"""
        types = get_args(BlockType)
        expected = {
            "thinking", "speech", "expression", "tool_call", "tool_result",
            "scene", "narration", "choice", "bgm", "emotion", "system",
            "turn_end", "error", "confirm",
            "memory_update", "workspace_update",
        }
        assert set(types) == expected
        assert len(types) == 16

    def test_invalid_block_type(self):
        """非法的 block_type 应该被拒绝"""
        with pytest.raises(ValidationError):
            Block(block_type="invalid_type")  # type: ignore


class TestBlock:
    def test_minimal_block(self):
        """最简 Block 应使用默认值"""
        block = Block(block_type="thinking")
        assert block.type == "block"
        assert block.block_type == "thinking"
        assert block.delta == ""
        assert block.is_final is True
        assert block.metadata == {}

    def test_full_block(self):
        """完整字段的 Block 序列化"""
        block = Block(
            block_type="speech",
            delta="Hello world",
            is_final=False,
            metadata={"emotion": "happy"},
        )
        assert block.type == "block"
        assert block.delta == "Hello world"
        assert block.is_final is False
        assert block.metadata == {"emotion": "happy"}

    def test_block_serialization_to_dict(self):
        """Block 可以序列化为 dict"""
        block = Block(block_type="tool_call", delta='{"fn": "test"}')
        data = block.model_dump()
        assert data["block_type"] == "tool_call"
        assert data["delta"] == '{"fn": "test"}'
        assert data["is_final"] is True

    def test_block_serialization_to_json(self):
        """Block 可以序列化为 JSON"""
        block = Block(block_type="error", delta="Something broke")
        json_str = block.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["block_type"] == "error"
        assert parsed["delta"] == "Something broke"
        assert parsed["type"] == "block"

    def test_block_deserialization_from_dict(self):
        """Block 可以从 dict 反序列化"""
        data = {
            "type": "block",
            "block_type": "scene",
            "delta": "beach_sunset",
            "is_final": True,
            "metadata": {"transition": "fade"},
        }
        block = Block(**data)
        assert block.block_type == "scene"
        assert block.delta == "beach_sunset"
        assert block.metadata["transition"] == "fade"

    def test_block_frozen(self):
        """Block 是不可变的 (frozen)"""
        block = Block(block_type="narration", delta="test")
        with pytest.raises(ValidationError):
            block.delta = "changed"  # type: ignore

    def test_all_block_types_constructable(self):
        """所有 13 种 BlockType 均可构造"""
        for bt in get_args(BlockType):
            block = Block(block_type=bt, delta=f"delta_{bt}")
            assert block.block_type == bt
            assert block.delta == f"delta_{bt}"

    def test_empty_metadata_default(self):
        """不传 metadata 时默认为空 dict"""
        block = Block(block_type="thinking")
        assert block.metadata == {}

    def test_block_equality_based_on_content(self):
        """相同内容的 Block 应相等 (frozen 使 model_dump 一致)"""
        b1 = Block(block_type="speech", delta="hi")
        b2 = Block(block_type="speech", delta="hi")
        assert b1.model_dump() == b2.model_dump()


class TestClientMessage:
    def test_minimal_client_message(self):
        """最简 ClientMessage"""
        msg = ClientMessage(
            type="user_message",
            character_id="ario",
        )
        assert msg.type == "user_message"
        assert msg.content is None
        assert msg.character_id == "ario"
        assert msg.mode == "single"
        assert msg.images == []

    def test_client_message_with_content(self):
        """带内容的 ClientMessage"""
        msg = ClientMessage(
            type="user_message",
            content="Hello!",
            character_id="ario",
            mode="group",
        )
        assert msg.content == "Hello!"
        assert msg.mode == "group"

    def test_client_message_ping_type(self):
        """支持 ping 和 disconnect 类型"""
        ping = ClientMessage(type="ping", character_id="ario")
        assert ping.type == "ping"

        dc = ClientMessage(type="disconnect", character_id="ario")
        assert dc.type == "disconnect"

    def test_client_message_with_images(self):
        """带图片的 ClientMessage"""
        msg = ClientMessage(
            type="user_message",
            content="Look at this",
            character_id="ario",
            images=["base64img1", "base64img2"],
        )
        assert len(msg.images) == 2
        assert msg.images == ["base64img1", "base64img2"]

    def test_client_message_serialization(self):
        """ClientMessage JSON 序列化"""
        msg = ClientMessage(type="user_message", character_id="ario", content="Hi")
        json_str = msg.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["type"] == "user_message"
        assert parsed["character_id"] == "ario"
        assert parsed["content"] == "Hi"
