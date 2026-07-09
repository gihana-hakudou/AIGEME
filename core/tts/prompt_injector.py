"""TTS 提示词注入器 — 注入到 variable content（不污染 system KV cache）"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TTSPromptInjector:
    """TTS 提示词注入器"""

    @staticmethod
    def build_variable_reminder(tts_config: dict[str, Any]) -> str | None:
        """构建本轮 TTS 格式提醒（注入到 variable content，不污染 system KV cache）

        注意：调用者需确保 TTS 已开启才调用此方法。
        """
        tone = tts_config.get("tone", "自然温和")
        return (
            "## 语音输出格式指导\n\n"
            "用户已开启 TTS 语音朗读功能。请将你的回复内容用 `<speak>` 标签包裹：\n\n"
            "```\n"
            "<speak tone=\"语气\">你的回复内容</speak>\n"
            "```\n\n"
            "**tone 支持**（可用 `+` 组合）：开心、悲伤、愤怒、兴奋、温柔、高冷、活泼、"
            "严肃、慵懒、俏皮、深沉、磁性、甜美、沙哑、东北话、四川话、粤语、唱歌、御姐音、"
            "大叔音、台湾腔 等\n\n"
            "**以下内容不放 speak 标签内**：代码块、网页链接/URL、表情/立绘标签（`<tachie-e>`）、"
            "动作描述、纯数字/表格数据\n\n"
            "**语言限制**：仅支持中文和英文，日语/韩语/法语等不要放 speak 内\n\n"
            f"**默认语气**: {tone}\n\n"
            "示例：\n"
            "```\n"
            "<speak tone=\"兴奋\">今天天气真好！我们去散步吧。</speak>\n"
            "具体的天气预报数据：25°C，湿度60%\n"
            "<speak tone=\"开心\">See you tomorrow!</speak>\n"
            "以下是日语原文：こんにちは\n"
            "<tachie-e>happy</tachie-e>\n"
            "```"
        )
