"""音频合并器 — 将多个 WAV 片段合并为完整 WAV，含交叉淡入淡出防爆音"""

import io
import logging
import struct
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# WAV 参数（MIMO 返回固定规格）
SAMPLE_RATE = 24000
BITS_PER_SAMPLE = 16
NUM_CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit = 2 bytes

# 交叉淡入淡出时长（毫秒）
CROSSFADE_MS = 5


def _samples_to_bytes(samples: list[int]) -> bytes:
    """16-bit PCM samples → bytes"""
    return struct.pack(f"<{len(samples)}h", *samples)


def _bytes_to_samples(data: bytes) -> list[int]:
    """bytes → 16-bit PCM samples"""
    count = len(data) // SAMPLE_WIDTH
    return list(struct.unpack(f"<{count}h", data))


def _build_wav_header(data_size: int) -> bytes:
    """构建 WAV 文件头（PCM, 16-bit, mono, 24000Hz）"""
    header = io.BytesIO()
    # RIFF header
    header.write(b"RIFF")
    header.write(struct.pack("<I", 36 + data_size))
    header.write(b"WAVE")
    # fmt chunk
    header.write(b"fmt ")
    header.write(struct.pack("<I", 16))               # chunk size
    header.write(struct.pack("<H", 1))                 # PCM
    header.write(struct.pack("<H", NUM_CHANNELS))      # mono
    header.write(struct.pack("<I", SAMPLE_RATE))       # sample rate
    byte_rate = SAMPLE_RATE * NUM_CHANNELS * SAMPLE_WIDTH
    header.write(struct.pack("<I", byte_rate))         # byte rate
    block_align = NUM_CHANNELS * SAMPLE_WIDTH
    header.write(struct.pack("<H", block_align))       # block align
    header.write(struct.pack("<H", BITS_PER_SAMPLE))   # bits per sample
    # data chunk
    header.write(b"data")
    header.write(struct.pack("<I", data_size))
    return header.getvalue()


def _strip_wav_header(wav_data: bytes) -> bytes:
    """去掉 WAV 文件头，只保留 PCM 数据"""
    # 跳过 RIFF header 找到 data 块
    pos = 12  # after RIFF+WAVE
    while pos < len(wav_data) - 8:
        chunk_id = wav_data[pos:pos + 4]
        chunk_size = struct.unpack("<I", wav_data[pos + 4:pos + 8])[0]
        if chunk_id == b"data":
            return wav_data[pos + 8:pos + 8 + chunk_size]
        pos += 8 + chunk_size
    # fallback: 尝试跳过标准 44 字节头
    if len(wav_data) > 44:
        return wav_data[44:]
    return wav_data


def merge_wavs(wav_chunks: Sequence[bytes], crossfade_ms: int = CROSSFADE_MS) -> bytes:
    """
    合并多个 WAV 音频块为单个完整 WAV。

    爆音处理：拼接点前后做短交叉淡入淡出。
    
    Args:
        wav_chunks: WAV 音频块列表（含文件头）
        crossfade_ms: 交叉淡入淡出时长（毫秒）

    Returns:
        完整的 WAV 音频数据（含文件头）
    """
    if not wav_chunks:
        return b""

    if len(wav_chunks) == 1:
        return wav_chunks[0]

    # 去掉头，提取 PCM 数据
    pcm_chunks = [_strip_wav_header(c) for c in wav_chunks]

    # 计算交叉淡入淡出的样本数
    fade_samples = int(SAMPLE_RATE * crossfade_ms / 1000)
    if fade_samples < 1:
        fade_samples = 1

    merged_samples: list[int] = []

    for i, pcm in enumerate(pcm_chunks):
        samples = _bytes_to_samples(pcm)

        if i == 0:
            # 第一个块：尾部淡出
            if len(samples) > fade_samples:
                fade_out_part = samples[-fade_samples:]
                rest = samples[:-fade_samples]
                merged_samples.extend(rest)

                # 暂存淡出部分等待与下一个块淡入叠加
                fade_buffer = fade_out_part
            else:
                merged_samples.extend(samples)
                fade_buffer = []
        else:
            # 非第一个块：头部淡入 → 叠加淡出缓冲区 → 尾部淡出（如果不是最后一块）
            fade_in_count = min(fade_samples, len(samples))

            # 头部淡入部分
            fade_in_part = samples[:fade_in_count]

            # 叠加：淡出缓冲区 + 淡入部分
            overlap_len = min(len(fade_buffer), len(fade_in_part))
            for j in range(overlap_len):
                gain_out = 1.0 - (j / overlap_len)  # 从 1→0 线性
                gain_in = j / overlap_len            # 从 0→1 线性
                sample = int(fade_buffer[j] * gain_out + fade_in_part[j] * gain_in)
                merged_samples.append(sample)

            # 如果淡出缓冲区更长，多余部分直接加（不应发生，但做保护）
            if len(fade_buffer) > overlap_len:
                for j in range(overlap_len, len(fade_buffer)):
                    gain_out = 1.0 - (j / len(fade_buffer))
                    merged_samples.append(int(fade_buffer[j] * gain_out))

            # 如果淡入部分更长，多余部分直接加
            if len(fade_in_part) > overlap_len:
                merged_samples.extend(fade_in_part[overlap_len:])

            # 中间部分（非淡入非淡出）
            middle = samples[fade_in_count:]
            if i < len(pcm_chunks) - 1 and len(middle) > fade_samples:
                # 不是最后一块：尾部淡出
                fade_out_part = middle[-fade_samples:]
                rest = middle[:-fade_samples]
                merged_samples.extend(rest)
                fade_buffer = fade_out_part
            else:
                # 最后一块或太短：直接加
                merged_samples.extend(middle)
                fade_buffer = []

    # 最后一帧淡出（防止结尾 click）
    if fade_buffer:
        fade_len = len(fade_buffer)
        for j in range(fade_len):
            gain = 1.0 - (j / fade_len)
            merged_samples.append(int(fade_buffer[j] * gain))

    # 构造完整 WAV
    pcm_data = _samples_to_bytes(merged_samples)
    wav_header = _build_wav_header(len(pcm_data))
    return wav_header + pcm_data


def save_turn_audio(character: str, turn_id: str, merged_wav: bytes,
                    cache_dir: Path) -> Path:
    """将合并后的 WAV 保存到角色缓存目录"""
    import time
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{turn_id}.wav"
    filepath = cache_dir / filename
    filepath.write_bytes(merged_wav)
    logger.info(f"[TTS] 保存轮次音频: {filepath} ({len(merged_wav)} bytes)")
    return filepath
