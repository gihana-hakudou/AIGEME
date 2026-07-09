"""
TTS 模块集成测试
运行: python -m pytest tests/test_tts_integration.py -v
或:   python tests/test_tts_integration.py
"""

import os
import sys
import json
import unittest
import tempfile
from pathlib import Path

# 确保项目在路径中
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── SpeakParser 测试 ──

class TestSpeakParser(unittest.TestCase):
    """SpeakParser 单元测试"""

    def setUp(self):
        from core.tts.speak_parser import SpeakParser
        self.parser_class = SpeakParser

    def _make_parser(self):
        return self.parser_class()

    def test_single_tag(self):
        """解析单个 speak 标签"""
        p = self._make_parser()
        result = p.feed('<speak tone="开心">今天天气真好！</speak>')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].tone, "开心")
        self.assertEqual(result[0].text, "今天天气真好！")
        self.assertEqual(result[0].tts_text, "(开心)今天天气真好！")
        self.assertEqual(result[0].index, 0)

    def test_no_tone(self):
        """没有 tone 属性的标签"""
        p = self._make_parser()
        result = p.feed('<speak>你好</speak>')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].tone, "")
        self.assertEqual(result[0].text, "你好")
        self.assertEqual(result[0].tts_text, "你好")

    def test_multi_tags(self):
        """多个 speak 标签连续出现"""
        p = self._make_parser()
        result = p.feed('<speak tone="开心">哈哈</speak><speak tone="悲伤">呜呜</speak>')
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].tone, "开心")
        self.assertEqual(result[0].text, "哈哈")
        self.assertEqual(result[1].tone, "悲伤")
        self.assertEqual(result[1].text, "呜呜")
        self.assertEqual(result[1].index, 1)

    def test_cross_chunk(self):
        """跨 chunk 解析"""
        p = self._make_parser()
        r1 = p.feed('<speak tone="兴奋">今天天气真')
        self.assertEqual(len(r1), 0)
        r2 = p.feed('好！</speak>')
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0].text, "今天天气真好！")

    def test_mixed_tags(self):
        """speak 标签与普通文本混合"""
        p = self._make_parser()
        result = p.feed('开头<speak tone="开心">中间</speak>结尾')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].text, "中间")
        clean = p.get_clean_text()
        self.assertIn("开头", clean)
        self.assertIn("结尾", clean)

    def test_complex_tone(self):
        """复合 tone（多个维度 + 连接）"""
        p = self._make_parser()
        result = p.feed('<speak tone="开心+御姐音">你好呀</speak>')
        self.assertEqual(result[0].tone, "开心+御姐音")
        self.assertEqual(result[0].tts_text, "(开心+御姐音)你好呀")

    def test_strip_tags(self):
        """strip_tags 静态方法"""
        text = '<speak tone="开心">今天天气真好！</speak>她说。'
        clean = self.parser_class.strip_tags(text)
        self.assertEqual(clean, "今天天气真好！她说。")

        text2 = '<speak>简单标签</speak>'
        clean2 = self.parser_class.strip_tags(text2)
        self.assertEqual(clean2, "简单标签")

    def test_flush_open_tag(self):
        """强制关闭未闭合标签"""
        p = self._make_parser()
        p.feed('<speak tone="悲伤">今天心情不好')
        flushed = p.flush_open_tag()
        self.assertIsNotNone(flushed)
        self.assertEqual(flushed.tone, "悲伤")
        self.assertEqual(flushed.text, "今天心情不好")

    def test_empty_delta(self):
        """空 delta 不应产生完成标签"""
        p = self._make_parser()
        result = p.feed("")
        self.assertEqual(len(result), 0)

    def test_no_tags(self):
        """不含 speak 标签的纯文本"""
        p = self._make_parser()
        result = p.feed("这是一段普通的文字，没有任何标签。")
        self.assertEqual(len(result), 0)
        clean = p.get_clean_text()
        self.assertIn("普通", clean)


# ── AudioMerger 测试 ──

class TestAudioMerger(unittest.TestCase):
    """AudioMerger 单元测试"""

    def setUp(self):
        from core.tts.audio_merger import merge_wavs, _strip_wav_header, _build_wav_header
        self.merge_wavs = merge_wavs
        self._strip_wav_header = _strip_wav_header
        self._build_wav_header = _build_wav_header

    def _make_dummy_wav(self, sample_count: int = 1000) -> bytes:
        """生成一段静音 WAV（仅用于测试合并逻辑）"""
        import struct
        data = struct.pack(f"<{sample_count}h", *([0] * sample_count))
        header = self._build_wav_header(len(data))
        return header + data

    def test_single_chunk(self):
        """单个 WAV 直接返回"""
        wav = self._make_dummy_wav(100)
        result = self.merge_wavs([wav])
        self.assertEqual(result, wav)

    def test_multi_chunk(self):
        """多个 WAV 合并"""
        wav1 = self._make_dummy_wav(500)
        wav2 = self._make_dummy_wav(500)
        result = self.merge_wavs([wav1, wav2])
        self.assertGreater(len(result), len(wav1))
        # 验证 WAV 头部
        self.assertTrue(result.startswith(b"RIFF"))
        self.assertIn(b"WAVE", result[:12])

    def test_empty_list(self):
        """空列表返回空 bytes"""
        result = self.merge_wavs([])
        self.assertEqual(result, b"")

    def test_strip_header(self):
        """WAV 头剥离"""
        wav = self._make_dummy_wav(100)
        pcm = self._strip_wav_header(wav)
        self.assertEqual(len(pcm), 100 * 2)  # 100 samples × 2 bytes (16-bit)


# ── 配置管理测试 ──

class TestTTSConfig(unittest.TestCase):
    """TTS 配置管理测试"""

    def setUp(self):
        from core.tts.config import TTSConfig, PRESET_VOICES
        self.TTSConfig = TTSConfig
        self.PRESET_VOICES = PRESET_VOICES

    def test_preset_voices(self):
        """预置音色列表完整"""
        self.assertIn("冰糖", self.PRESET_VOICES)
        self.assertIn("茉莉", self.PRESET_VOICES)
        self.assertIn("Mia", self.PRESET_VOICES)
        self.assertEqual(len(self.PRESET_VOICES), 9)

    def test_load_config_defaults(self):
        """加载角色配置应返回默认值"""
        config = self.TTSConfig.load("ario")
        self.assertIn("mode", config)
        self.assertIn("voice", config)
        self.assertIn("tone", config)
        self.assertEqual(config["voice"], "冰糖")
        # mode 取决于实际写入的角色配置——可能是用户保存的 voice_clone

    def test_get_cache_dir(self):
        """缓存目录路径正确"""
        cache_dir = self.TTSConfig.get_cache_dir("ario")
        self.assertTrue(str(cache_dir).endswith("tts-wav"))
        self.assertTrue(cache_dir.parent.name == "ario")


# ── API 模块测试 ──

class TestTTSAPI(unittest.TestCase):
    """TTS API 模块测试"""

    def test_routes_registered(self):
        """TTS 路由已注册"""
        from core.main import create_app
        app = create_app()
        tts_routes = [r.path for r in app.routes if '/api/tts' in str(r.path)]
        self.assertEqual(len(tts_routes), 5)  # voices + config(GET) + config(PUT) + test + cache
        self.assertIn("/api/tts/voices", tts_routes)
        self.assertIn("/api/tts/config", tts_routes)
        self.assertIn("/api/tts/test", tts_routes)

    def test_blocks_type_extended(self):
        """BlockType 包含 TTS 类型"""
        from core.protocols.blocks import BlockType
        btypes = BlockType.__args__
        self.assertIn("audio", btypes)
        self.assertIn("audio_play_end", btypes)
        self.assertIn("tts_state", btypes)

    def test_module_imports(self):
        """所有 TTS 模块可导入"""
        from core.tts.config import TTSConfig
        from core.tts.client import MimoTTSClient, TTSResult
        from core.tts.speak_parser import SpeakParser, CompletedSpeak
        from core.tts.speak_queue import SpeakQueue
        from core.tts.audio_merger import merge_wavs
        from core.tts.prompt_injector import TTSPromptInjector
        self.assertTrue(callable(merge_wavs))


# ── PromptInjector 测试 ──

class TestPromptInjector(unittest.TestCase):
    """提示词注入器测试"""

    def setUp(self):
        from core.tts.prompt_injector import TTSPromptInjector
        self.injector = TTSPromptInjector

    def test_build_instruction(self):
        """variable reminder 应包含完整格式指导"""
        config = {"enabled": True, "tone": "自然温和"}
        reminder = self.injector.build_variable_reminder(config)
        self.assertIn("<speak", reminder)
        self.assertIn("tone", reminder)
        self.assertIn("开心", reminder)
        self.assertIn("东北话", reminder)
        self.assertIn("唱歌", reminder)

    def test_variable_reminder_enabled(self):
        """提醒包含指定语气"""
        config = {"enabled": True, "tone": "兴奋"}
        reminder = self.injector.build_variable_reminder(config)
        self.assertIsNotNone(reminder)
        self.assertIn("兴奋", reminder)

    def test_variable_reminder_disabled(self):
        """调用者控制开关，注入器不再检查 enabled"""
        config = {"enabled": False, "tone": "自然温和"}
        reminder = self.injector.build_variable_reminder(config)
        self.assertIsNotNone(reminder)
        self.assertIn("语音输出格式指导", reminder)
        self.assertIn("自然温和", reminder)


# ── 循环 Hook 验证 ──

class TestLoopHook(unittest.TestCase):
    """检查 loop.py 中的 TTS Hook 代码是否存在"""

    def test_tts_hook_in_loop(self):
        """验证 loop.py 包含 TTS 相关代码"""
        loop_path = Path(__file__).parent.parent / "core" / "raact_loop" / "loop.py"
        content = loop_path.read_text("utf-8")
        self.assertIn("TTSConfig", content)
        self.assertIn("SpeakParser", content)
        self.assertIn("SpeakQueue", content)
        self.assertIn("_tts_send_block", content)
        self.assertIn("_tts_parser", content)
        self.assertIn("turn_end", content)
        self.assertIn("finish_turn", content)
        # TTS 提示词注入到 variable content（不污染 system KV cache）
        self.assertIn("build_variable_reminder", content)

    def test_tts_in_context_py(self):
        """验证 context.py 不再残留 TTS 注入代码（已迁移到 loop.py）"""
        ctx_path = Path(__file__).parent.parent / "core" / "engine" / "context.py"
        content = ctx_path.read_text("utf-8")
        # TTS 注入已从 context.py 移除，不应包含 TTS 特定代码
        self.assertNotIn("_build_tts_reminder", content)
        self.assertNotIn("_build_tts_system_instruction", content)


# ── 前端文件验证 ──

class TestFrontendFiles(unittest.TestCase):
    """前端文件存在性和关键内容验证"""

    def test_tts_js_exists(self):
        """tts.js 文件存在"""
        path = Path(__file__).parent.parent / "frontend" / "chat" / "js" / "tts.js"
        self.assertTrue(path.exists(), "tts.js 不存在")
        content = path.read_text("utf-8")
        self.assertIn("TTSPlayer", content)
        self.assertIn("play", content)
        self.assertIn("interrupt", content)
        self.assertIn("stop", content)

    def test_blocks_js_has_audio(self):
        """blocks.js 包含 audio handler"""
        path = Path(__file__).parent.parent / "frontend" / "chat" / "js" / "blocks.js"
        content = path.read_text("utf-8")
        self.assertIn("_handleAudio", content)
        self.assertIn("'audio'", content)

    def test_state_js_has_tts_interrupt(self):
        """state.js 包含 TTS 中断调用"""
        path = Path(__file__).parent.parent / "frontend" / "chat" / "js" / "state.js"
        content = path.read_text("utf-8")
        self.assertIn("TTSPlayer.interrupt", content)
        self.assertIn("tts_enabled", content)

    def test_index_html_has_tts(self):
        """index.html 包含 TTS 相关元素"""
        path = Path(__file__).parent.parent / "frontend" / "chat" / "index.html"
        content = path.read_text("utf-8")
        self.assertIn("tts.js", content)
        self.assertIn("tts-toggle", content)
        self.assertIn("tts-api-key", content)
        self.assertIn("tts-mode", content)
        self.assertIn("btn-test-tts", content)


if __name__ == "__main__":
    # 抑制非关键日志
    import logging
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
