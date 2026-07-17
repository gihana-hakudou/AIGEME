"""TTS HTTP API 路由 — 配置管理、音色列表、测试合成"""

import base64
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException

from core.tts.config import TTSConfig, PRESET_VOICES
from core.tts.client import MimoTTSClient

# ── 安全校验 ──

_CHARACTER_PATTERN = re.compile(r'^[a-zA-Z0-9_\-.]+$')


def _validate_character(name: str) -> str:
    """校验 character 参数，防止路径遍历"""
    if not _CHARACTER_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="Invalid character name")
    return name


# ── 字段白名单 ──

ALLOWED_TTS_FIELDS = frozenset({
    "enabled", "mode", "voice", "voice_design_prompt",
    "voice_clone_sample", "voice_clone_style_desc", "tone",
    "voice_clone_sample_b64",
})

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tts"])


@router.get("/api/tts/voices")
async def list_preset_voices() -> dict:
    """获取预置音色列表"""
    return {"voices": PRESET_VOICES}


@router.get("/api/tts/config")
async def get_tts_config(character: str = "ario") -> dict:
    """获取指定角色的 TTS 配置"""
    character = _validate_character(character)
    config = TTSConfig.load(character)
    # 返回给前端时移除敏感字段和内部字段（base64 太大不传回前端）
    safe = {k: v for k, v in config.items() if k not in ("api_key", "voice_clone_sample_b64")}
    safe["has_api_key"] = bool(config.get("api_key"))
    safe["has_sample"] = bool(config.get("voice_clone_sample"))
    safe["character"] = character
    return safe


@router.put("/api/tts/config")
async def update_tts_config(data: dict) -> dict:
    """更新 TTS 配置"""
    character = _validate_character(data.pop("character", "ario"))
    api_key = data.pop("api_key", None)

    # 如果提供了 api_key，保存到 local.yaml
    if api_key is not None:
        TTSConfig.save_api_key(api_key)

    # 字段白名单：只允许写入已知字段
    safe_data = {k: v for k, v in data.items() if k in ALLOWED_TTS_FIELDS}
    unknown = set(data) - ALLOWED_TTS_FIELDS
    if unknown and logger.isEnabledFor(logging.WARNING):
        logger.warning(f"[TTS] 忽略未知配置字段: {unknown}")

    # 保存角色配置
    if safe_data:
        TTSConfig.save(character, safe_data)

    return {"status": "ok", "message": "TTS 配置已更新"}


@router.post("/api/tts/test")
async def test_tts(data: dict) -> dict:
    """测试 TTS 合成"""
    text = data.get("text", "你好，欢迎体验小米智能语音合成。")
    character = _validate_character(data.get("character", "ario"))

    # 优先使用前端传入的 api_key（未保存时也能测试）
    inline_api_key = (data.get("api_key") or "").strip()

    # 合并配置：前端传入的参数覆盖角色配置
    if inline_api_key:
        char_config = {"mode": "preset", "voice": "冰糖", "tone": "",
                       "api_key": inline_api_key}
    else:
        char_config = TTSConfig.load(character)

    for k, v in data.get("config", {}).items():
        if v is not None:
            char_config[k] = v

    # 统一 voice_clone_sample 键名（前端可能传 voice_clone_sample，后端期望 voice_clone_sample_b64）
    if "voice_clone_sample" in char_config and "voice_clone_sample_b64" not in char_config:
        char_config["voice_clone_sample_b64"] = char_config.pop("voice_clone_sample")

    # 检查 API Key
    api_key = inline_api_key or char_config.get("api_key")
    if not api_key:
        return {"status": "error", "message": "请先配置 MIMO API Key"}

    try:
        client = MimoTTSClient(api_key)
        result = client.test_synthesize(text, char_config)
        audio_b64 = base64.b64encode(result.audio_data).decode("utf-8")
        return {
            "status": "ok",
            "audio_data": audio_b64,
            "format": result.format,
            "size_bytes": len(result.audio_data),
        }
    except Exception as e:
        logger.exception(f"[TTS] 测试合成失败")
        # 对已知 API 错误提供友好提示
        err_msg = str(e)
        if "402" in err_msg and "insufficient_balance" in err_msg:
            err_msg = "MIMO API 账户余额不足，请充值后重试"
        elif "401" in err_msg or "unauthorized" in err_msg.lower():
            err_msg = "MIMO API Key 无效，请检查并重新配置"
        elif "403" in err_msg or "forbidden" in err_msg.lower():
            err_msg = "MIMO API Key 权限不足"
        elif "429" in err_msg or "rate_limit" in err_msg.lower():
            err_msg = "MIMO API 请求频率过高，请稍后重试"
        return {"status": "error", "message": err_msg}


@router.get("/api/tts/cache/{character}/{turn_id}")
async def get_cached_audio(character: str, turn_id: str) -> dict:
    """获取缓存的 TTS 音频（用于重播）"""
    character = _validate_character(character)
    from core.tts.audio_merger import _build_wav_header
    cache_dir = TTSConfig.get_cache_dir(character)
    # 查找匹配 turn_id 的缓存文件
    pattern = f"*_{turn_id}.wav"
    matches = sorted(cache_dir.glob(pattern))
    if not matches:
        return {"status": "error", "message": "缓存未找到"}

    latest = matches[-1]
    try:
        audio_bytes = latest.read_bytes()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return {
            "status": "ok",
            "audio_data": audio_b64,
            "format": "wav",
            "size_bytes": len(audio_bytes),
        }
    except Exception as e:
        logger.warning(f"[TTS] 缓存读取失败: {e}")
        return {"status": "error", "message": "音频缓存读取失败，请重试"}
