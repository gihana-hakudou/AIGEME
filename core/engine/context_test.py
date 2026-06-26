"""Tests for PromptAssembler — 主要测试 _replace_current_time 方法"""

import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from core.engine.context import PromptAssembler
from core.tools.registry import ToolRegistry


@pytest.fixture
def mock_assembler():
    """创建最小 PromptAssembler 实例"""
    registry = MagicMock(spec=ToolRegistry)
    registry.schemas = []
    registry.get = MagicMock(return_value=None)
    return PromptAssembler(
        character_dir=Path("/fake/char"),
        user_md_path=Path("/fake/user.md"),
        system_prompt_path=Path("/fake/system.md"),
        tools_registry=registry,
    )


class TestReplaceCurrentTime:
    """_replace_current_time 方法测试"""

    def test_replace_placeholder(self, mock_assembler):
        """应正确替换 {{current_time}} 占位符"""
        text = "现在是 {{current_time}}，天气很好。"
        result = mock_assembler._replace_current_time(text)
        assert "{{current_time}}" not in result
        assert "现在是 " in result
        assert "，天气很好。" in result

    def test_time_format(self, mock_assembler):
        """替换后的时间格式应为 YYYY年MM月DD日 HH:MM 星期X"""
        result = mock_assembler._replace_current_time("{{current_time}}")
        pattern = r'^\d{4}年\d{2}月\d{2}日 \d{2}:\d{2} \w{2,3}$'
        assert re.match(pattern, result), f"格式不匹配: {result}"

    def test_no_placeholder_unchanged(self, mock_assembler):
        """不包含占位符的文本应原样返回"""
        text = "这是一个没有时间占位符的文本。"
        result = mock_assembler._replace_current_time(text)
        assert result == text

    def test_multiple_placeholders(self, mock_assembler):
        """多个占位符应全部替换"""
        text = "时间1: {{current_time}}，时间2: {{current_time}}"
        result = mock_assembler._replace_current_time(text)
        assert "{{current_time}}" not in result
        # 两个占位符都被替换为时间字符串
        assert "时间1: " in result
        assert "时间2: " in result
        # 验证两个日期时间格式存在
        time_pattern = r'\d{4}年\d{2}月\d{2}日 \d{2}:\d{2} \w{2,3}'
        matches = re.findall(time_pattern, result)
        assert len(matches) == 2, f"应找到2个时间字符串，实际找到{len(matches)}: {matches}"

    def test_placeholder_in_mixed_content(self, mock_assembler):
        """占位符在混合内容中应被正确替换"""
        text = "系统提示：当前时间为 {{current_time}}\n请根据时间做出回应。"
        result = mock_assembler._replace_current_time(text)
        assert "{{current_time}}" not in result
        assert result.startswith("系统提示：当前时间为 ")
        assert "\n请根据时间做出回应。" in result

    def test_empty_string(self, mock_assembler):
        """空字符串应返回空字符串"""
        assert mock_assembler._replace_current_time("") == ""

    def test_weekday_is_chinese(self, mock_assembler):
        """星期应是中文（星期一至星期日）"""
        result = mock_assembler._replace_current_time("{{current_time}}")
        weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
        assert any(day in result for day in weekdays), f"未找到中文字符串: {result}"


class TestPromptAssemblerBuild:
    """PromptAssembler.build_system_prompt 集成测试"""

    @pytest.fixture
    def mock_deps(self):
        """创建最小依赖的 mock 环境"""
        from core.tools.base import BaseTool

        class _MockTool(BaseTool):
            name = "test_tool"
            description = "A test tool"
            parameters = {"type": "object", "properties": {}, "required": []}
            async def execute(self, **kwargs):
                return "ok"

        registry = ToolRegistry()
        registry.register(_MockTool())
        return {
            "character_dir": Path("/fake/character/ario"),
            "user_md_path": Path("/fake/user.md"),
            "system_prompt_path": Path("/fake/system.md"),
            "tools_registry": registry,
        }

    @patch.object(Path, "exists", return_value=True)
    @patch.object(Path, "read_text", return_value="# System\n\n现在时间: {{current_time}}\n")
    def test_build_system_prompt_keeps_time_placeholder(self, mock_read, mock_exists, mock_deps):
        """build_system_prompt 不再替换 {{current_time}}
        
        时间注入已迁移到 loop.py 中用户消息后的 meta 信息，不再注入 system prompt。
        这样可以避免每轮 system prompt 变化导致 KV 缓存失效。
        """
        assembler = PromptAssembler(**mock_deps)
        result = assembler.build_system_prompt()
        # {{current_time}} 不再被替换，保持原样
        assert "{{current_time}}" in result
