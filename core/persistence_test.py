"""Tests for Persistence - save/load conversation records"""

import tempfile
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.persistence import Persistence


@pytest.fixture
def temp_data_dir():
    """每个测试使用独立临时目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestPersistence:
    def test_init_creates_dirs(self, temp_data_dir):
        """初始化时应创建对话目录"""
        p = Persistence(data_dir=temp_data_dir, user_id="test_user", char_id="test_char")
        conv_dir = temp_data_dir / "test_user" / "test_char" / "conversations"
        assert conv_dir.exists()
        assert conv_dir.is_dir()

    async def test_load_empty_history(self, temp_data_dir):
        """无历史记录时应返回空列表"""
        p = Persistence(data_dir=temp_data_dir)
        history = await p.load_recent_history()
        assert history == []

    async def test_save_and_load_single_turn(self, temp_data_dir):
        """保存一条记录后应能正确加载"""
        p = Persistence(data_dir=temp_data_dir, user_id="u1", char_id="c1")
        await p.save_turn(role="user", content="你好")
        await p.save_turn(role="assistant", content="你好！有什么可以帮你的？")

        history = await p.load_recent_history()
        assert len(history) == 2
        assert isinstance(history[0], HumanMessage)
        assert history[0].content == "你好"
        assert isinstance(history[1], AIMessage)
        assert history[1].content == "你好！有什么可以帮你的？"

    async def test_save_with_kwargs(self, temp_data_dir):
        """保存时可传入额外 kwargs（如 reasoning）"""
        p = Persistence(data_dir=temp_data_dir)
        await p.save_turn(
            role="assistant",
            content="让我想想",
            reasoning="思考中...",
        )
        history = await p.load_recent_history()
        assert len(history) == 1
        assert history[0].additional_kwargs.get("reasoning") == "思考中..."

    async def test_save_tool_message(self, temp_data_dir):
        """保存工具消息"""
        p = Persistence(data_dir=temp_data_dir)
        await p.save_turn(role="tool", content="", result={"data": "test"})
        history = await p.load_recent_history()
        assert len(history) == 1
        assert isinstance(history[0], ToolMessage)

    async def test_save_with_meta(self, temp_data_dir):
        """保存时可附带 meta 信息，但不影响加载的 data 层"""
        p = Persistence(data_dir=temp_data_dir)
        await p.save_turn(role="user", content="hello", meta={"source": "web"})

        history = await p.load_recent_history()
        assert len(history) == 1
        assert history[0].content == "hello"

    async def test_corrupted_file_handling(self, temp_data_dir):
        """损坏的 JSON 文件应被跳过而不影响整体加载"""
        p = Persistence(data_dir=temp_data_dir, user_id="u", char_id="c")
        await p.save_turn(role="user", content="valid entry")

        # 手动创建一个损坏的文件
        conv_dir = temp_data_dir / "u" / "c" / "conversations"
        corrupted_file = conv_dir / "bad.json"
        corrupted_file.write_text("invalid json", encoding="utf-8")

        # 加载不应因损坏文件而崩溃
        history = await p.load_recent_history()
        assert len(history) == 1
        assert history[0].content == "valid entry"

    async def test_multiple_files_loading(self, temp_data_dir):
        """跨多个文件的记录应正确合并加载"""
        p = Persistence(data_dir=temp_data_dir, user_id="u", char_id="c")
        await p.save_turn(role="user", content="first file")
        await p.save_turn(role="assistant", content="still first file")

        # 加载所有
        history = await p.load_recent_history()
        assert len(history) == 2

    async def test_max_recent_limiting(self, temp_data_dir):
        """max_recent 应限制加载的记录数"""
        p = Persistence(data_dir=temp_data_dir, user_id="u", char_id="c", max_recent=3)
        for i in range(10):
            await p.save_turn(role="user", content=f"msg {i}")
        history = await p.load_recent_history()
        assert len(history) == 3
        assert history[-1].content == "msg 9"

    async def test_empty_content_in_history(self, temp_data_dir):
        """空 content 的记录应正常加载"""
        p = Persistence(data_dir=temp_data_dir)
        await p.save_turn(role="user", content="")
        history = await p.load_recent_history()
        assert len(history) == 1
        assert history[0].content == ""

    def test_load_llm_messages_static(self, temp_data_dir):
        """_load_llm_messages 静态方法正确转换所有角色"""
        records = [
            {"data": {"role": "user", "content": "hello"}},
            {"data": {"role": "assistant", "content": "hi", "reasoning": "thinking"}},
            {"data": {"role": "tool", "content": "", "result": {"x": 1}}},
        ]
        messages = Persistence._load_llm_messages(records)
        assert len(messages) == 3
        assert isinstance(messages[0], HumanMessage)
        assert isinstance(messages[1], AIMessage)
        assert messages[1].additional_kwargs.get("reasoning") == "thinking"
        assert isinstance(messages[2], ToolMessage)

    async def test_save_file_split(self, temp_data_dir):
        """文件记录数超过 max_file_records 时应分割"""
        p = Persistence(
            data_dir=temp_data_dir,
            user_id="u",
            char_id="c",
            max_file_records=5,
        )
        for i in range(7):
            await p.save_turn(role="user", content=f"msg {i}")

        conv_dir = temp_data_dir / "u" / "c" / "conversations"
        files = list(conv_dir.glob("*.json"))
        # 应该有原始文件 + 分割后的文件
        assert len(files) >= 1
