"""<speak> 标签流式解析器 — 实时解析 LLM 输出中的 TTS 标记"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# <speak tone="X">text</speak>
SPEAK_START_PATTERN = re.compile(r'<speak(?:\s+tone="([^"]*)")?\s*>')
SPEAK_END_TAG = "</speak>"
SPEAK_ALL_PATTERN = re.compile(r'<speak[^>]*>|</speak>')


@dataclass
class CompletedSpeak:
    """一个已完成的 <speak> 标签"""
    text: str          # 标签内纯净文本（无标签、无 tone 前缀）
    tts_text: str      # 发送给 TTS API 的文本（含 (语气) 前缀）
    tone: str          # 语气（从 tone 属性提取）
    index: int         # 标签序号（用于顺序保证）


class SpeakParser:
    """
    流式解析器：从 LLM 流式输出中实时解析 <speak tone="X"> 标签。

    使用方式：
        parser = SpeakParser()
        for delta in stream:
            completed = parser.feed(delta)
            for speak in completed:
                await queue.enqueue(speak)
        clean_text = parser.get_clean_text()
    """

    def __init__(self):
        self._buffer = ""               # 未匹配的文本缓存
        self._current_text = ""         # 当前正在收集的 tag 内部文本
        self._current_tone = ""         # 当前 tag 的 tone 属性
        self._in_tag = False            # 是否在 speak 标签内
        self._tag_index = 0             # 全局标签序号
        self._clean_parts: list[str] = []  # 已剥离标签的纯净文本片段

    def feed(self, delta: str) -> list[CompletedSpeak]:
        """输入流式片段，返回已完成的 Speak 标签列表"""
        self._buffer += delta
        completed_list: list[CompletedSpeak] = []

        while self._buffer:
            if not self._in_tag:
                # 查找 <speak 开头
                match = SPEAK_START_PATTERN.search(self._buffer)
                if not match:
                    # 没有新标签，当前 buffer 全部是纯净文本
                    self._clean_parts.append(self._buffer)
                    self._buffer = ""
                    break

                # 标签前的文本是纯净文本
                before = self._buffer[:match.start()]
                if before:
                    self._clean_parts.append(before)

                # 进入标签
                self._in_tag = True
                self._current_tone = match.group(1) or ""
                self._current_text = ""
                self._buffer = self._buffer[match.end():]
            else:
                # 在标签内，查找闭合标签
                end_pos = self._buffer.find(SPEAK_END_TAG)
                if end_pos == -1:
                    # 还没闭合，暂存当前文本
                    self._current_text += self._buffer
                    self._buffer = ""
                    break

                # 闭合前的文本
                inner_text = self._buffer[:end_pos]
                self._current_text += inner_text

                # 完成一个 speak 标签
                tone = self._current_tone
                text = self._current_text
                tts_text = f"({tone}){text}" if tone else text

                completed = CompletedSpeak(
                    text=text,
                    tts_text=tts_text,
                    tone=tone,
                    index=self._tag_index,
                )
                self._tag_index += 1
                completed_list.append(completed)

                # 闭合标签后的文本后续当纯净文本处理
                after = self._buffer[end_pos + len(SPEAK_END_TAG):]
                self._buffer = after
                self._in_tag = False
                self._current_text = ""
                self._current_tone = ""

        return completed_list

    def get_clean_text(self) -> str:
        """获取已剥离所有标签的纯净文本"""
        parts = list(self._clean_parts)

        # 如果还有未闭合的标签内文本，丢弃（不渲染）
        if not self._in_tag and self._buffer:
            parts.append(self._buffer)

        return "".join(parts)

    @property
    def has_open_tag(self) -> bool:
        """是否还有未闭合的 speak 标签"""
        return self._in_tag

    def flush_open_tag(self) -> CompletedSpeak | None:
        """强制关闭当前未闭合的 speak 标签，返回完成的标签（给 turn_end 用）"""
        if not self._in_tag or not self._current_text:
            return None

        text = self._current_text
        tone = self._current_tone
        tts_text = f"({tone}){text}" if tone else text

        completed = CompletedSpeak(
            text=text,
            tts_text=tts_text,
            tone=tone,
            index=self._tag_index,
        )
        self._tag_index += 1
        self._in_tag = False
        self._current_text = ""
        self._current_tone = ""
        return completed

    @property
    def tag_count(self) -> int:
        """已完成的标签数量"""
        return self._tag_index

    @staticmethod
    def strip_tags(text: str) -> str:
        """移除所有 <speak ...> 和 </speak> 标签，保留纯文本"""
        return SPEAK_ALL_PATTERN.sub('', text).strip()

    @staticmethod
    def tone_to_prefix(tone: str) -> str:
        """将 tone 属性转为 MIMO API 接受的 (语气) 前缀"""
        return f"({tone})" if tone else ""
