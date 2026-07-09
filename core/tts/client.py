"""小米 MiMo TTS API 客户端 — 基于 OpenAI 兼容格式"""

import base64
import logging
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from .config import TTSConfig, PRESET_VOICES

logger = logging.getLogger(__name__)

MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"


@dataclass
class TTSResult:
    """TTS 合成结果"""
    audio_data: bytes        # WAV 原始音频
    duration_ms: int = 0     # 音频时长（毫秒），由 API 估算
    format: str = "wav"      # 音频格式


class MimoTTSClient:
    """小米 MIMO TTS API 客户端"""

    def __init__(self, api_key: str | None = None):
        self._api_key = (api_key or TTSConfig.get_api_key()).strip()
        self._client = OpenAI(api_key=self._api_key, base_url=MIMO_BASE_URL)

    # ── 音色列表 ──

    @staticmethod
    def list_preset_voices() -> dict[str, str]:
        """返回预置音色列表"""
        return dict(PRESET_VOICES)

    # ── 合成 ──

    def synthesize(self, text: str, config: dict[str, Any]) -> TTSResult:
        """根据配置合成语音

        Args:
            text: 待合成文本（可含 (语气) 前缀）
            config: TTS 配置字典（来自 TTSConfig.load()）

        Returns:
            TTSResult 包含 WAV 音频数据
        """
        mode = config.get("mode", "preset")
        voice = config.get("voice") or "冰糖"
        tone_guide = config.get("tone") or ""

        # voice_design 和 voice_clone 模式：tone 以 (语气) 前缀放在文本开头
        if tone_guide and mode in ("voice_design", "voice_clone"):
            text = f"({tone_guide}){text}"

        # 根据 mode 构建 API 参数
        if mode == "voice_design":
            design_prompt = config.get("voice_design_prompt", "")
            if not design_prompt:
                logger.warning("[TTS] voice_design 模式但未提供音色描述，使用默认")
                design_prompt = "自然温和的声音"
            return self._synthesize_voice_design(text, design_prompt)
        elif mode == "voice_clone":
            sample_base64 = config.get("voice_clone_sample_b64") or ""
            style_desc = config.get("voice_clone_style_desc") or ""
            if not sample_base64:
                logger.error(f"[TTS] voice_clone 模式缺少音频样本，降级为 preset voice='{voice}'")
                return self._synthesize_preset(text, voice, tone_guide)
            return self._synthesize_voice_clone(text, sample_base64, style_desc)
        else:
            return self._synthesize_preset(text, voice, tone_guide)

    def test_synthesize(self, text: str, config: dict[str, Any]) -> TTSResult:
        """测试合成（与 synthesize 相同，专为测试按钮）"""
        return self.synthesize(text, config)

    # ── 内部方法 ──

    def _synthesize_preset(self, text: str, voice: str, tone_guide: str) -> TTSResult:
        """预置音色合成"""
        messages = [{"role": "assistant", "content": text}]
        if tone_guide:
            messages.insert(0, {"role": "user", "content": tone_guide})

        resp = self._client.chat.completions.create(
            model="mimo-v2.5-tts",
            messages=messages,
            audio={"format": "wav", "voice": voice},
        )
        return self._parse_response(resp)

    def _synthesize_voice_design(self, text: str, design_prompt: str) -> TTSResult:
        """文本设计音色合成"""
        resp = self._client.chat.completions.create(
            model="mimo-v2.5-tts-voicedesign",
            messages=[
                {"role": "user", "content": design_prompt},
                {"role": "assistant", "content": text},
            ],
            audio={"format": "wav"},
        )
        return self._parse_response(resp)

    def _synthesize_voice_clone(self, text: str, sample_base64: str,
                                style_desc: str = "") -> TTSResult:
        """语音克隆合成"""
        if not sample_base64:
            logger.error("[TTS] 语音克隆需要音频样本 base64")
            raise ValueError("voice_clone mode requires audio sample")

        messages = [{"role": "assistant", "content": text}]
        if style_desc:
            messages.insert(0, {"role": "user", "content": style_desc})

        resp = self._client.chat.completions.create(
            model="mimo-v2.5-tts-voiceclone",
            messages=messages,
            audio={"format": "wav", "voice": sample_base64},
        )
        return self._parse_response(resp)

    @staticmethod
    def _parse_response(resp) -> TTSResult:
        """解析 API 响应，提取音频数据"""
        audio_b64 = resp.choices[0].message.audio.data
        audio_bytes = base64.b64decode(audio_b64)
        return TTSResult(audio_data=audio_bytes, format="wav")
