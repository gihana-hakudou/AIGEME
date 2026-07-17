"""小米 MiMo TTS API 客户端 — 基于 OpenAI 兼容格式"""

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import OpenAI

from .config import TTSConfig, PRESET_VOICES

logger = logging.getLogger(__name__)

MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"

# OpenAI HTTP 客户端超时（秒）
_TTS_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


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
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=MIMO_BASE_URL,
            timeout=_TTS_REQUEST_TIMEOUT,
        )

    # ── 音色列表 ──

    @staticmethod
    def list_preset_voices() -> dict[str, str]:
        """返回预置音色列表"""
        return dict(PRESET_VOICES)

    # ── 合成 ──

    def synthesize(self, text: str, config: dict[str, Any],
                   segment_tone: str = "") -> TTSResult:
        """根据配置合成语音

        Args:
            text: 待合成文本（纯净文本，不含 tone 前缀）
            config: TTS 配置字典（来自 TTSConfig.load()）
            segment_tone: 来自 <speak tone="X"> 的 per-segment 语气，
                          优先于 config 的默认 tone

        Returns:
            TTSResult 包含 WAV 音频数据
        """
        mode = config.get("mode", "preset")
        voice = config.get("voice") or "冰糖"
        config_tone = config.get("tone") or ""

        # per-segment tone 优先于 config 默认 tone
        effective_tone = segment_tone or config_tone

        # voice_design 和 voice_clone 模式：tone 以 (语气) 前缀放在文本开头
        if effective_tone and mode in ("voice_design", "voice_clone"):
            text = f"({effective_tone}){text}"

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
                return self._synthesize_preset(text, voice, effective_tone)
            return self._synthesize_voice_clone(text, sample_base64, style_desc)
        else:
            return self._synthesize_preset(text, voice, effective_tone)

    def test_synthesize(self, text: str, config: dict[str, Any],
                        segment_tone: str = "") -> TTSResult:
        """测试合成（与 synthesize 相同，专为测试按钮）"""
        return self.synthesize(text, config, segment_tone)

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
    def _parse_response(resp) -> "TTSResult":
        """解析 API 响应，提取音频数据"""
        try:
            choice = resp.choices[0]
            if not choice.message or not choice.message.audio:
                raise ValueError("API 响应缺少 audio 字段")
            audio_b64 = choice.message.audio.data
            if not audio_b64:
                raise ValueError("API 返回空音频数据")
            audio_bytes = base64.b64decode(audio_b64)
            return TTSResult(audio_data=audio_bytes, format="wav")
        except (AttributeError, IndexError, KeyError, ValueError, TypeError) as e:
            logger.error(f"[TTS] 解析 API 响应失败: {e}", exc_info=True)
            raise ValueError(f"TTS API 响应解析失败: {e}") from e
