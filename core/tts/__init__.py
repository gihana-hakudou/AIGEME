"""TTS 语音合成模块 — 小米 MiMo API 封装"""

from .config import TTSConfig
from .client import MimoTTSClient

__all__ = ["TTSConfig", "MimoTTSClient"]
