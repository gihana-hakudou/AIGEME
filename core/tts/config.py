"""TTS 配置管理 — 按角色存储在 character/<角色>/config.yaml，全局 API Key 在 local.yaml"""

import base64
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from core.utils import PROJECT_ROOT

logger = logging.getLogger(__name__)

# local.yaml 中的 tts 配置键
_LOCAL_TTS_KEY = "tts"
# character config.yaml 中的 tts 配置键
_CHAR_TTS_KEY = "tts"

# 默认 TTS 配置（每个角色的默认值）
_DEFAULT_TTS_CONFIG: dict[str, Any] = {
    "enabled": False,
    "mode": "preset",
    "voice": "冰糖",
    "voice_design_prompt": "",
    "voice_clone_sample": None,
    "voice_clone_style_desc": "",
}

# 预置音色列表
PRESET_VOICES: dict[str, str] = {
    "mimo_default": "MiMo-默认",
    "冰糖": "冰糖 (中文女声)",
    "茉莉": "茉莉 (中文女声)",
    "苏打": "苏打 (中文男声)",
    "白桦": "白桦 (中文男声)",
    "Mia": "Mia (英文女声)",
    "Chloe": "Chloe (英文女声)",
    "Milo": "Milo (英文男声)",
    "Dean": "Dean (英文男声)",
}

# MIME → 文件扩展名映射（精确匹配）
_MIME_TO_EXT: dict[str, str] = {
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
}

# 语音克隆样本大小上限（10MB base64 数据）
_MAX_SAMPLE_BASE64_LEN = 14_000_000  # ~10MB raw → ~14MB base64


def _get_local_config_path() -> Path:
    """获取 local.yaml 路径"""
    return PROJECT_ROOT / ".AIGEME" / "local.yaml"


def _get_char_config_path(character: str) -> Path:
    """获取角色 config.yaml 路径"""
    return PROJECT_ROOT / "character" / character / "config.yaml"


def _get_sample_dir(character: str) -> Path:
    """获取语音克隆样本存储目录"""
    return PROJECT_ROOT / ".AIGEME" / ".data" / "local" / character / "tts-samples"


def _guess_mime(suffix: str) -> str:
    """根据文件扩展名猜测 MIME 类型"""
    mime_map = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg"}
    return mime_map.get(suffix.lower(), "audio/wav")


class TTSConfig:
    """TTS 配置管理器"""

    @staticmethod
    def get_api_key() -> str:
        """从 local.yaml 读取 MIMO API Key"""
        local_path = _get_local_config_path()
        if not local_path.exists():
            return ""
        try:
            data = yaml.safe_load(local_path.read_text("utf-8")) or {}
            tts_cfg = data.get(_LOCAL_TTS_KEY, {}) or {}
            return (tts_cfg.get("api_key") or "").strip()
        except Exception as e:
            logger.warning(f"[TTS] 读取 API Key 失败: {e}")
            return ""

    @staticmethod
    def save_api_key(api_key: str) -> None:
        """保存 API Key 到 local.yaml"""
        local_path = _get_local_config_path()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {}
            if local_path.exists():
                data = yaml.safe_load(local_path.read_text("utf-8")) or {}
            if _LOCAL_TTS_KEY not in data or not isinstance(data[_LOCAL_TTS_KEY], dict):
                data[_LOCAL_TTS_KEY] = {}
            data[_LOCAL_TTS_KEY]["api_key"] = api_key
            local_path.write_text(yaml.dump(data, allow_unicode=True), "utf-8")
        except Exception as e:
            logger.error(f"[TTS] 保存 API Key 失败: {e}")

    @staticmethod
    def load(character: str) -> dict[str, Any]:
        """加载指定角色的 TTS 配置（合并全局 api_key + 自动加载克隆样本）"""
        config = dict(_DEFAULT_TTS_CONFIG)  # 先克隆默认值

        # 从角色 config.yaml 读取
        char_path = _get_char_config_path(character)
        if char_path.exists():
            try:
                char_data = yaml.safe_load(char_path.read_text("utf-8")) or {}
                char_tts = char_data.get(_CHAR_TTS_KEY, {}) or {}
                config.update(char_tts)
            except Exception as e:
                logger.warning(f"[TTS] 读取角色配置失败: {e}")

        # 语音克隆模式：自动从样本文件加载 base64
        if config.get("mode") == "voice_clone":
            sample_filename = config.get("voice_clone_sample") or ""
            if sample_filename:
                sample_path = _get_sample_dir(character) / sample_filename
                if sample_path.exists():
                    try:
                        sample_bytes = sample_path.read_bytes()
                        sample_b64 = base64.b64encode(sample_bytes).decode("ascii")
                        mime = _guess_mime(sample_path.suffix)
                        config["voice_clone_sample_b64"] = f"data:{mime};base64,{sample_b64}"
                        logger.info(f"[TTS] 已加载克隆样本: {sample_filename} ({len(sample_bytes)} bytes)")
                    except Exception as e:
                        logger.warning(f"[TTS] 读取样本文件失败: {e}")

        # 合并全局 API Key
        api_key = TTSConfig.get_api_key()
        config["api_key"] = api_key

        return config

    @staticmethod
    def save(character: str, overrides: dict[str, Any]) -> None:
        """保存角色 TTS 配置到 character/<角色>/config.yaml

        如果 overrides 中包含 voice_clone_sample 且是 base64 data URL，
        自动解码保存为文件，config.yaml 只存文件名。
        """
        char_path = _get_char_config_path(character)
        if not char_path.exists():
            logger.warning(f"[TTS] 角色目录不存在: {character}")
            return

        try:
            data = yaml.safe_load(char_path.read_text("utf-8")) or {}
            if _CHAR_TTS_KEY not in data or not isinstance(data[_CHAR_TTS_KEY], dict):
                data[_CHAR_TTS_KEY] = {}

            # 处理语音克隆样本：base64 → 文件
            sample_data = overrides.get("voice_clone_sample")
            is_data_url = isinstance(sample_data, str) and sample_data.startswith("data:")
            if is_data_url:
                # 解码 base64 data URL 并保存为文件
                try:
                    match = re.match(r'data:([^;]+);base64,(.+)', sample_data)
                    if match:
                        mime, b64_data = match.groups()

                        # 安全校验：base64 大小上限
                        if len(b64_data) > _MAX_SAMPLE_BASE64_LEN:
                            raise ValueError(
                                f"语音克隆样本过大 ({len(b64_data)} bytes base64, "
                                f"上限 {_MAX_SAMPLE_BASE64_LEN})"
                            )

                        sample_bytes = base64.b64decode(b64_data)

                        sample_dir = _get_sample_dir(character)
                        sample_dir.mkdir(parents=True, exist_ok=True)

                        # 精确 MIME 匹配
                        ext = _MIME_TO_EXT.get(mime, ".wav")

                        sample_filename = f"voice_sample{ext}"
                        sample_path = sample_dir / sample_filename
                        sample_path.write_bytes(sample_bytes)
                        logger.info(f"[TTS] 已保存克隆样本: {sample_path} ({len(sample_bytes)} bytes)")

                        # config.yaml 存文件名
                        data[_CHAR_TTS_KEY]["voice_clone_sample"] = sample_filename
                except Exception as e:
                    logger.error(f"[TTS] 保存样本文件失败: {e}")
                # 从 overrides 移除原始 base64，避免写入 YAML（不影响调用者，get 不修改原 dict）
                if "voice_clone_sample" in overrides:
                    del overrides["voice_clone_sample"]

            # 只覆盖提供的字段（不删除未提供的）
            for k, v in overrides.items():
                if k in ("api_key", "tone"):
                    continue  # api_key + tone 不走角色配置（tone 是测试参数，不持久化）
                data[_CHAR_TTS_KEY][k] = v

            char_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), "utf-8")
        except Exception as e:
            logger.error(f"[TTS] 保存角色配置失败: {e}")

    @staticmethod
    def get_cache_dir(character: str) -> Path:
        """获取 TTS 音频缓存目录：.AIGEME/.data/local/<角色名>/tts-wav/"""
        cache_dir = PROJECT_ROOT / ".AIGEME" / ".data" / "local" / character / "tts-wav"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir
