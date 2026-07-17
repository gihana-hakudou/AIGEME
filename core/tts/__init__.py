"""TTS 语音合成模块 — 小米 MiMo API 封装"""

from .config import TTSConfig

__all__ = ["TTSConfig", "MimoTTSClient"]


def MimoTTSClient(*args, **kwargs):
    """惰性导入 MimoTTSClient，避免强制安装 openai"""
    from .client import MimoTTSClient as _MimoTTSClient
    return _MimoTTSClient(*args, **kwargs)
