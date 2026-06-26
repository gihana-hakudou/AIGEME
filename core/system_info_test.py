"""Tests for system_info.py — 系统环境信息生成"""

import tempfile
from pathlib import Path

from core.system_info import generate_system_info


class TestGenerateSystemInfo:
    """generate_system_info 整体测试"""

    def test_output_contains_required_sections(self):
        """输出应包含操作系统和 Python 段落"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_system_info(Path(tmpdir))

        assert "# 系统环境" in result
        assert "## 操作系统" in result
        assert "## Python" in result

    def test_python_path_points_to_venv(self):
        """Python 路径应指向 venv 下的 python.exe"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_system_info(Path(tmpdir))

        lines = result.split("\n")
        python_line = next(line for line in lines if "python.exe" in line)
        assert "venv" in python_line or "Scripts" in python_line
        assert python_line.strip().startswith("- ")

    def test_os_info_present(self):
        """操作系统信息应非空"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_system_info(Path(tmpdir))

        lines = result.split("\n")
        os_line = None
        for i, line in enumerate(lines):
            if line.strip() == "## 操作系统":
                os_line = lines[i + 1]
                break
        assert os_line is not None
        assert len(os_line.strip()) > len("- ")

    def test_writes_to_file(self):
        """应写入到正确的文件路径"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_system_info(Path(tmpdir))
            output_path = Path(tmpdir) / ".AIGEME" / ".data" / "system" / "system_info.md"
            assert output_path.exists()
            content = output_path.read_text("utf-8")
            assert content == result