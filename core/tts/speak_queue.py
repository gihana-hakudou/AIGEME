"""Speak 标签队列调度器 — 按 index 严格顺序调度 TTS 合成与播放"""

import asyncio
import logging
from typing import Callable, Any

from core.protocols.blocks import Block
from core.tts.config import TTSConfig
from core.tts.client import MimoTTSClient, TTSResult
from core.tts.speak_parser import CompletedSpeak
from core.tts.audio_merger import merge_wavs, save_turn_audio

logger = logging.getLogger(__name__)


class SpeakQueue:
    """
    Speak 标签队列调度器。

    职责：
    1. 接收 parser 产出的 CompletedSpeak
    2. 按 index 顺序调度 TTS 合成
    3. 合成完成后通过 send_block 向前端下发 audio block
    4. 整轮结束后合并所有音频并缓存

    关键行为：
    - 顺序保证：后合成的等前一个播完再播
    - 中断：flush() 清空队列，取消正在合成的请求
    - 语言过滤：跳过非中英文内容（不合成，递增序号继续）
    """

    def __init__(self, character: str, send_block: Callable[[Block], Any],
                 api_key: str | None = None, config: dict | None = None):
        self._character = character
        self._send_block = send_block
        self._api_key = api_key

        self._client = MimoTTSClient(api_key)
        # 优先使用传入的 config（含前端热加载覆盖），否则从 YAML 加载
        self._config = config if config is not None else TTSConfig.load(character)

        # 队列状态
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self._processing_task: asyncio.Task | None = None
        self._pending: dict[int, bytes] = {}  # 已合成但未轮到的音频
        self._all_chunks: list[bytes] = []    # 本轮所有音频块（用于合并缓存）
        self._next_play_index = 0
        self._next_enqueue_index = 0
        self._cancelled = False
        self._turn_id: str = ""

    def set_turn_id(self, turn_id: str) -> None:
        """设置当前轮次 ID"""
        self._turn_id = turn_id

    async def enqueue(self, speak: CompletedSpeak) -> None:
        """添加一个 speak 到队列"""
        if self._cancelled:
            return
        item = QueueItem(speak=speak, index=speak.index)
        await self._queue.put(item)
        self._next_enqueue_index = speak.index + 1

        # 确保处理任务在运行
        if self._processing_task is None or self._processing_task.done():
            self._processing_task = asyncio.create_task(self._process_loop())

    async def flush(self) -> None:
        """清空队列（新一轮输出时调用）"""
        self._cancelled = True

        # 取消正在处理的任务
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        # 清空 asyncio.Queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._pending.clear()
        self._all_chunks.clear()
        self._next_play_index = 0
        self._next_enqueue_index = 0
        self._cancelled = False

    async def finish_turn(self) -> None:
        """等待当前队列处理完毕，然后合并缓存"""
        # 等待处理任务完成
        if self._processing_task and not self._processing_task.done():
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        # 合并并缓存
        if len(self._all_chunks) > 0 and self._turn_id:
            try:
                merged = merge_wavs(self._all_chunks)
                cache_dir = TTSConfig.get_cache_dir(self._character)
                save_turn_audio(self._character, self._turn_id, merged, cache_dir)
                logger.info(f"[TTS] 轮次 {self._turn_id} 音频已缓存 ({len(merged)} bytes)")
            except Exception as e:
                logger.warning(f"[TTS] 合并缓存失败: {e}")

    # ── 内部 ──

    async def _process_loop(self) -> None:
        """后台任务：逐个处理队列中的 speak"""
        try:
            while not self._cancelled:
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # 队列空了，退出
                    break

                if self._cancelled:
                    break

                try:
                    await self._process_item(item)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"[TTS] speak[{item.index}] 处理失败: {e}")
                    # 跳过，继续下一个
                    self._next_play_index = item.index + 1
        except asyncio.CancelledError:
            logger.debug("[TTS] 队列处理被取消")

    async def _process_item(self, item: "QueueItem") -> None:
        """处理单个 speak 条目：合成 → 等待播放 → 下发"""
        # 如果这个 index 已经过期（被 flush 重置过），跳过
        if item.index < self._next_play_index:
            logger.debug(f"[TTS] 跳过过期 speak[{item.index}]")
            return

        # 检查语言是否支持（中英文）
        if not self._is_supported_language(item.speak.text):
            logger.info(f"[TTS] 跳过非中英文: {item.speak.text[:20]}...")
            self._next_play_index = item.index + 1
            return

        # TTS 合成
        logger.info(f"[TTS] 合成 speak[{item.index}]: {item.speak.text[:30]}...")
        tts_text = item.speak.tts_text
        result: TTSResult = await asyncio.to_thread(
            self._client.synthesize, tts_text, self._config
        )

        if self._cancelled:
            return

        # 存入本轮记录
        self._all_chunks.append(result.audio_data)

        # 等待轮到本 index 播放
        while self._next_play_index < item.index and not self._cancelled:
            # 暂存已合成的音频
            self._pending[item.index] = result.audio_data
            await asyncio.sleep(0.05)
            continue

        if self._cancelled:
            return

        # 轮到了，发送 audio block
        import base64
        audio_b64 = base64.b64encode(result.audio_data).decode("utf-8")
        audio_block = Block(
            block_type="audio",
            delta=audio_b64,
            is_final=False,
            metadata={
                "index": item.index,
                "format": "wav",
                "character": self._character,
            },
        )
        await self._send_block(audio_block)
        self._next_play_index = item.index + 1

    @staticmethod
    def _is_supported_language(text: str) -> bool:
        """检查文本是否包含不支持的语言（日语假名等）"""
        import re
        if re.search(r'[\u3040-\u309F\u30A0-\u30FF]', text):  # 平假名+片假名
            return False
        if re.search(r'[\uAC00-\uD7AF]', text):  # 韩语
            return False
        if re.search(r'[\u0400-\u04FF]', text):  # 西里尔字母
            return False
        return True


class QueueItem:
    """队列中的待处理项"""
    def __init__(self, speak: CompletedSpeak, index: int):
        self.speak = speak
        self.index = index
