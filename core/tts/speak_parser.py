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

    关键设计：只要检测到 <speak 就进入标签模式（不要求标签语法完整），
    累计到 </speak> 闭合后再统一解析，天然支持跨 chunk 标签边界。

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
        self._current_text = ""         # 当前正在收集的 tag 内部文本（含 <speak...> 语法）
        self._current_tone = ""         # 当前 tag 的 tone 属性
        self._in_tag = False            # 是否在 speak 标签内（含 <speak...> 语法）
        self._tag_index = 0             # 全局标签序号
        self._clean_parts: list[str] = []  # 已剥离标签的纯净文本片段

    def feed(self, delta: str) -> list[CompletedSpeak]:
        """输入流式片段，返回已完成的 Speak 标签列表"""
        self._buffer += delta
        completed_list: list[CompletedSpeak] = []

        while self._buffer:
            if not self._in_tag:
                # 查找 <speak 位置（不要求完整 >，天然支持跨 chunk）
                speak_pos = self._buffer.find('<speak')
                if speak_pos == -1:
                    # 没有 <speak，全部是纯净文本
                    self._clean_parts.append(self._buffer)
                    self._buffer = ""
                    break

                # <speak 之前的文本是纯净文本
                before = self._buffer[:speak_pos]
                if before:
                    self._clean_parts.append(before)

                # 进入标签模式：从 <speak 开始累计，等 </speak> 闭合再解析
                self._in_tag = True
                self._current_tone = ""
                self._buffer = self._buffer[speak_pos:]  # 保留 buffer 让 else 分支处理
                # 不 break，直接 fall through 到 else 分支处理标签内容
            else:
                # 在标签内：把 buffer 追加到暂存文本
                if self._buffer:
                    self._current_text += self._buffer
                    self._buffer = ""

                # 查找 </speak>
                end_pos = self._current_text.find(SPEAK_END_TAG)
                if end_pos == -1:
                    break  # 还没闭合，等下一 chunk

                # 有完整的 <speak...>inner</speak> 了
                full = self._current_text[:end_pos + len(SPEAK_END_TAG)]

                # 解析 <speak...> 提取 tone
                gt_pos = full.find('>')
                if gt_pos >= 0:
                    opening_tag = full[:gt_pos + 1]
                    tag_match = SPEAK_START_PATTERN.match(opening_tag)
                    tone = tag_match.group(1) if tag_match and tag_match.group(1) else ""
                    inner_text = full[gt_pos + 1:end_pos]
                else:
                    # 从未看到 >（异常），跳过
                    tone = ""
                    inner_text = ""

                if inner_text.strip():
                    completed = CompletedSpeak(
                        text=inner_text,
                        tts_text=f"({tone}){inner_text}" if tone else inner_text,
                        tone=tone,
                        index=self._tag_index,
                    )
                    self._tag_index += 1
                    completed_list.append(completed)
                    # 标签内文本也加入纯净文本，供前端显示（含 _tts_send_block 的增量推送）
                    self._clean_parts.append(inner_text)

                # 闭合标签后的文本
                after = self._current_text[end_pos + len(SPEAK_END_TAG):]
                self._buffer = after
                self._current_text = ""
                self._current_tone = ""
                self._in_tag = False

        return completed_list

    def get_clean_text(self) -> str:
        """获取已剥离所有标签的纯净文本"""
        parts = list(self._clean_parts)

        # _in_tag 为 True 时，_current_text 中的标签内容不输出给前端
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

        # 从 _current_text 中剥离 <speak...> 语法，提取 inner text 和 tone
        text = self._current_text
        gt_pos = text.find('>')
        if gt_pos >= 0:
            inner_text = text[gt_pos + 1:]
            opening_tag = text[:gt_pos + 1]
            tag_match = SPEAK_START_PATTERN.match(opening_tag)
            tone = tag_match.group(1) if tag_match and tag_match.group(1) else ""
        else:
            inner_text = ""
            tone = ""

        if not inner_text or not inner_text.strip():
            return None

        completed = CompletedSpeak(
            text=inner_text,
            tts_text=f"({tone}){inner_text}" if tone else inner_text,
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
