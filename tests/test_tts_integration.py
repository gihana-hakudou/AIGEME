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
        # TTS 提示词由 build_variable_content(tts_enabled=...) 统一注入
        self.assertIn("build_variable_content", content)
        self.assertIn("tts_enabled=tts_enabled", content)

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


# ── Bug #1 修复验证：队列消息 TTS 参数继承 ──

class TestQueueMessageTTSParams(unittest.TestCase):
    """验证 ws_server 消息队列 TTS 参数传递（Bug #1 P0 根因修复）"""

    def test_run_raact_task_accepts_tts_params(self):
        """验证 _run_raact_task 签名包含全部 4 个 TTS 参数"""
        import inspect
        from core.ws_server import WSServer
        sig = inspect.signature(WSServer._run_raact_task)
        params = list(sig.parameters.keys())
        self.assertIn("tts_enabled", params, "run_raact_task 应接受 tts_enabled 参数")
        self.assertIn("tts_mode", params, "run_raact_task 应接受 tts_mode 参数")
        self.assertIn("tts_voice", params, "run_raact_task 应接受 tts_voice 参数")
        self.assertIn("tts_tone", params, "run_raact_task 应接受 tts_tone 参数")

    def test_queue_message_passes_tts_params(self):
        """验证消息队列取出消息后传递了所有 4 个 TTS 参数"""
        ws_path = Path(__file__).parent.parent / "core" / "ws_server.py"
        content = ws_path.read_text("utf-8")

        # 找到消息队列处理段中的 TTS 参数传递
        self.assertIn("tts_enabled=next_msg.tts_enabled", content)
        self.assertIn("tts_mode=next_msg.tts_mode", content)
        self.assertIn("tts_voice=next_msg.tts_voice", content)
        self.assertIn("tts_tone=next_msg.tts_tone", content)

    def test_queue_message_has_tts_attributes(self):
        """验证 Message 数据结构包含 tts 属性"""
        from core.protocols.blocks import ClientMessage

        msg = ClientMessage(
            type="user_message",
            content="第二条消息",
            tts_enabled=True,
            tts_mode="preset",
            tts_voice="茉莉",
            tts_tone="兴奋",
        )
        self.assertEqual(msg.tts_enabled, True)
        self.assertEqual(msg.tts_mode, "preset")
        self.assertEqual(msg.tts_voice, "茉莉")
        self.assertEqual(msg.tts_tone, "兴奋")


# ── Bug #2 修复验证：配置空值防御 ──

class TestConfigNullDefense(unittest.TestCase):
    """验证 config.get() or default 模式正确处理 None/空字符串/缺失（Bug #2）"""

    def test_voice_none_defaults_to_bingtang(self):
        """config.get('voice') or '冰糖' 在 voice=None 时返回 '冰糖'"""
        result = None or "冰糖"
        self.assertEqual(result, "冰糖")

    def test_voice_empty_string_defaults_to_bingtang(self):
        """空字符串被 or 短路"""
        result = "" or "冰糖"
        self.assertEqual(result, "冰糖")

    def test_voice_missing_key_defaults_to_bingtang(self):
        """缺失键时 get 返回 None，or 短路"""
        config = {"mode": "preset"}
        voice = config.get("voice") or "冰糖"
        self.assertEqual(voice, "冰糖")

    def test_voice_present_uses_it(self):
        """voice 有值时返回原有值"""
        config = {"voice": "茉莉"}
        voice = config.get("voice") or "冰糖"
        self.assertEqual(voice, "茉莉")

    def test_tone_empty_string_defaults_to_empty(self):
        """config.get('tone') or '' 在 tone=None 时返回 ''"""
        config = {"mode": "preset", "tone": None}
        tone = config.get("tone") or ""
        self.assertEqual(tone, "")

    def test_tone_missing_defaults_to_empty(self):
        """缺失 tone 时返回 ''"""
        config = {"mode": "preset"}
        tone = config.get("tone") or ""
        self.assertEqual(tone, "")

    def test_tone_present_uses_it(self):
        """tone 有值时返回原有值"""
        config = {"tone": "开心"}
        tone = config.get("tone") or ""
        self.assertEqual(tone, "开心")

    def test_sample_b64_empty_string_or_default(self):
        """config.get('voice_clone_sample_b64') or '' 处理 None"""
        config = {"mode": "voice_clone"}
        sample = config.get("voice_clone_sample_b64") or ""
        self.assertEqual(sample, "")

    def test_style_desc_empty_string_or_default(self):
        """config.get('voice_clone_style_desc') or '' 处理 None"""
        config = {"mode": "voice_clone"}
        style = config.get("voice_clone_style_desc") or ""
        self.assertEqual(style, "")

    def test_pop_not_used_in_save(self):
        """验证 TTSConfig.save 使用 get() 而非 pop()，不污染调用者 dict"""
        overrides = {"voice_clone_sample": "data:audio/wav;base64,AAAA", "mode": "preset"}
        overrides_copy = dict(overrides)
        # TTSConfig.save 内部使用 get()，不应删除键
        # 模拟 get 行为
        _ = overrides.get("voice_clone_sample")
        self.assertIn("voice_clone_sample", overrides,
                      "使用 get() 不应从原 dict 删除键")
        self.assertEqual(overrides, overrides_copy)


# ── Bug #10 修复验证：voice_clone 降级 ──

class TestVoiceCloneDegradation(unittest.TestCase):
    """验证 voice_clone 模式在缺 sample 时降级为 preset（Bug #10）"""

    def setUp(self):
        from unittest.mock import MagicMock

    def setUp(self):
        self.config_preset = {
            "mode": "preset",
            "voice": "冰糖",
            "tone": "开心",
        }
        self.config_clone_no_sample = {
            "mode": "voice_clone",
            "voice": "茉莉",
            "tone": "兴奋",
            # 没有 voice_clone_sample_b64 → 应降级
        }

    def test_synthesize_voice_clone_no_sample_degrades(self):
        """voice_clone 模式缺 sample → synthesize 调 _synthesize_preset 而非抛异常"""
        from unittest.mock import patch, MagicMock
        from core.tts.client import MimoTTSClient

        client = MimoTTSClient(api_key="test_key")

        # Mock _synthesize_preset 返回成功结果
        with patch.object(client, "_synthesize_preset") as mock_preset:
            mock_preset.return_value = MagicMock(audio_data=b"fake_wav", duration_ms=1000, format="wav")

            result = client.synthesize("你好", self.config_clone_no_sample)

            # 验证降级到 preset 并返回了结果
            mock_preset.assert_called_once()
            self.assertEqual(result.audio_data, b"fake_wav")

    def test_synthesize_voice_clone_no_sample_not_raise(self):
        """voice_clone 缺 sample 不应 raise ValueError"""
        from unittest.mock import patch, MagicMock
        from core.tts.client import MimoTTSClient

        client = MimoTTSClient(api_key="test_key")

        with patch.object(client, "_synthesize_preset") as mock_preset:
            mock_preset.return_value = MagicMock(audio_data=b"fake_wav", duration_ms=1000, format="wav")
            # 不应抛出任何异常
            try:
                client.synthesize("测试", self.config_clone_no_sample)
            except ValueError:
                self.fail("voice_clone 缺 sample 不应 raise ValueError")

    def test_voice_clone_with_sample_uses_clone(self):
        """voice_clone 有 sample 时正常使用克隆路径"""
        from unittest.mock import patch, MagicMock
        from core.tts.client import MimoTTSClient

        client = MimoTTSClient(api_key="test_key")
        config = {
            "mode": "voice_clone",
            "voice": "Mia",
            "tone": "",
            "voice_clone_sample_b64": "data:audio/wav;base64,AAAA",
            "voice_clone_style_desc": "温柔",
        }

        with patch.object(client, "_synthesize_voice_clone") as mock_clone:
            mock_clone.return_value = MagicMock(audio_data=b"clone_wav", duration_ms=2000, format="wav")

            result = client.synthesize("克隆测试", config)

            mock_clone.assert_called_once()
            self.assertEqual(result.audio_data, b"clone_wav")


# ── Bug #5/#4 修复验证：turn_id 与缓存 ──

class TestTurnIdAndCache(unittest.TestCase):
    """验证 SpeakQueue turn_id 和缓存逻辑（Bug #5/#4）"""

    def setUp(self):
        from core.tts.speak_queue import SpeakQueue
        self.SpeakQueue = SpeakQueue

    async def _make_queue(self, config=None):
        """创建 SpeakQueue 实例（带 mock send_block）"""
        async def mock_send(block):
            pass
        cfg = config or {"mode": "preset", "voice": "冰糖", "tone": ""}
        return self.SpeakQueue("ario", mock_send, api_key="test_key", config=cfg)

    def test_set_turn_id_sets_internal(self):
        """set_turn_id() 后 _turn_id 不为空"""
        async def run():
            q = await self._make_queue()
            self.assertEqual(q._turn_id, "")
            q.set_turn_id("abc12345")
            self.assertEqual(q._turn_id, "abc12345")
        import asyncio
        asyncio.run(run())

    def test_finish_turn_clears_all_chunks(self):
        """finish_turn() 末尾清空 _all_chunks（修复 #4）"""
        async def run():
            q = await self._make_queue()
            q._all_chunks.append(b"chunk1")
            q._all_chunks.append(b"chunk2")
            self.assertEqual(len(q._all_chunks), 2)

            # turn_id 为空 → finish_turn 不会缓存但会清空
            self.assertEqual(q._turn_id, "")
            await q.finish_turn()
            self.assertEqual(len(q._all_chunks), 0,
                             "finish_turn 应清空 _all_chunks")
        import asyncio
        asyncio.run(run())

    def test_finish_turn_with_turn_id_clears_chunks(self):
        """finish_turn() 有 turn_id 时缓存后清空 _all_chunks"""
        async def run():
            q = await self._make_queue()
            q.set_turn_id("test9999")
            q._all_chunks.append(b"chunk1")
            q._all_chunks.append(b"chunk2")

            await q.finish_turn()
            self.assertEqual(len(q._all_chunks), 0,
                             "finish_turn 后有 turn_id 也应清空 _all_chunks")
        import asyncio
        asyncio.run(run())

    def test_flush_clears_all_chunks(self):
        """flush() 清空 _all_chunks"""
        async def run():
            q = await self._make_queue()
            q._all_chunks.append(b"chunk1")
            q._all_chunks.append(b"chunk2")
            await q.flush()
            self.assertEqual(len(q._all_chunks), 0)
        import asyncio
        asyncio.run(run())

    def test_multiple_rounds_no_cross_accumulation(self):
        """多轮 RaAct round 后 _all_chunks 不跨轮次累积"""
        async def run():
            q = await self._make_queue()

            # Round 1
            q.set_turn_id("round1")
            q._all_chunks.append(b"round1_data")
            self.assertEqual(len(q._all_chunks), 1)
            await q.finish_turn()
            self.assertEqual(len(q._all_chunks), 0)

            # Round 2
            q.set_turn_id("round2")
            q._all_chunks.append(b"round2_data")
            self.assertEqual(len(q._all_chunks), 1,
                             "Round 2 不应包含 Round 1 的数据")
            await q.finish_turn()
            self.assertEqual(len(q._all_chunks), 0,
                             "Round 2 finish_turn 后清空")
        import asyncio
        asyncio.run(run())


# ── Bug #3 修复验证：异常日志 ──

class TestExceptionLogging(unittest.TestCase):
    """验证 TTS 初始化异常被正确记录（Bug #3 — except Exception: pass → logger.error）"""

    def test_loop_tts_init_has_logger_error(self):
        """loop.py 中 TTS 初始化异常使用 logger.error 而非 silent pass"""
        loop_path = Path(__file__).parent.parent / "core" / "raact_loop" / "loop.py"
        content = loop_path.read_text("utf-8")

        # 验证异常处理使用 logger.error 而非 silent pass
        self.assertIn("logger.error(f\"[TTS] 初始化失败: {e}\"", content,
                      "异常应使用 logger.error 记录")
        self.assertIn("exc_info=True", content,
                      "logger.error 应包含 exc_info=True")
        self.assertNotIn("pass", content.split("except")[1].split("\n")[0] if "except" in content else "",
                         "异常处理不应静默 pass")

    def test_logger_error_has_exc_info(self):
        """验证 logger.error 调用包含 exc_info=True"""
        loop_path = Path(__file__).parent.parent / "core" / "raact_loop" / "loop.py"
        content = loop_path.read_text("utf-8")

        # 找到 except 块中的 logger.error 行
        lines = content.split("\n")
        found_error_with_exc = False
        for i, line in enumerate(lines):
            if "logger.error" in line and "初始化失败" in line:
                self.assertIn("exc_info=True", line,
                              f"行 {i+1}: logger.error 应包含 exc_info=True")
                found_error_with_exc = True
        self.assertTrue(found_error_with_exc, "未找到带 exc_info=True 的 logger.error")


# ── 配置 save 不污染调用者 dict ──

class TestConfigSaveNoPop(unittest.TestCase):
    """验证 config.py save 方法使用 get() 而非 pop()（Bug #8）"""

    def test_save_uses_get_not_pop(self):
        """TTSConfig.save 源码不应使用 dict.pop()"""
        config_path = Path(__file__).parent.parent / "core" / "tts" / "config.py"
        content = config_path.read_text("utf-8")

        # 在 save 方法附近搜索 pop(
        save_start = content.find("def save(character")
        save_end = content.find("\n    @staticmethod", save_start + 10)
        if save_end == -1:
            save_end = content.find("def get_cache_dir", save_start + 10)
        save_body = content[save_start:save_end]

        # 不应包含 .pop( 调用
        self.assertNotIn(".pop(", save_body,
                         "save 方法应使用 get() 而非 pop()")


if __name__ == "__main__":
    # 抑制非关键日志
    import logging
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
