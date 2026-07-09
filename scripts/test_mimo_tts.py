#!/usr/bin/env python3
"""
小米 MiMo TTS 测试脚本
- 测试预置音色合成 (mimo-v2.5-tts)
- 测试语音克隆合成 (mimo-v2.5-tts-voiceclone)

用法:
    python scripts/test_mimo_tts.py                     # 测试预置音色
    python scripts/test_mimo_tts.py --voiceclone        # 测试语音克隆（需提供音频样本）
    python scripts/test_mimo_tts.py --all               # 全部测试
"""

import os
import sys
import base64
import json
import time
import argparse
from pathlib import Path
from openai import OpenAI

# ── 配置 ──────────────────────────────────────────────────────────

API_KEY = "sk-ckmkntxlz51r88im8wlc2qzxuc2j306amadqy43nhltwp4uy"
BASE_URL = "https://api.xiaomimimo.com/v1"
OUTPUT_DIR = Path(__file__).parent.parent / "test_outputs" / "tts_test"

# 可用的预置音色（中文 + 英文）
PRESET_VOICES = {
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

# 测试文本（LLM 输出格式示范：<speak tone="语气">文本</speak>）
TEST_TEXT_ZH = """你好，欢迎体验小米智能语音合成。今天天气晴朗，气温适宜，是一个适合外出散步的好日子。"""

TEST_TEXT_EN = """Hello! Welcome to Xiaomi MiMo audio platform. Today we're testing our text-to-speech synthesis capabilities with natural and expressive voices."""

TEST_TEXT_STYLED = """(东北话)今天天气可好了，阳光老刺眼了，微风呼呼的。
北京今天晴，温度22到28度，空气贼好，出去溜达溜达呗！"""

# 带语气标签的测试文本
# LLM 输出 <speak tone="X"> 后端解析后将 tone 转为 (X) 前缀发送给 MIMO API
TEST_TEXT_WITH_TONES = """(开心)今天天气真好，阳光明媚！
(温柔)我们一起去散步吧。
[轻笑]你说是不是呀？"""

# 跨语言测试文本
TEST_TEXT_JA = """こんにちは、今日は良い天気ですね。桜がとても綺麗です。
ちょっと暑いけど、散歩には最適な日です。"""
TEST_TEXT_MIXED = """今天天气真好！
Hello, how are you today?
こんにちは、元気ですか？
Bonjour, comment allez-vous?"""


# ── 工具函数 ──────────────────────────────────────────────────────

def init_client() -> OpenAI:
    """初始化 OpenAI 兼容客户端"""
    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


def save_audio_from_response(response, voice_name: str, label: str = "") -> Path:
    """从 API 响应中提取音频数据并保存为 WAV 文件"""
    audio_b64 = response.choices[0].message.audio.data
    audio_bytes = base64.b64decode(audio_b64)

    safe_name = voice_name.replace("/", "_").replace(" ", "_")
    suffix = f"_{label}" if label else ""
    filename = f"tts_{safe_name}{suffix}.wav"
    filepath = OUTPUT_DIR / filename

    with open(filepath, "wb") as f:
        f.write(audio_bytes)

    print(f"  ✅ 已保存: {filename}  ({len(audio_bytes)} bytes)")
    return filepath


def read_audio_base64(filepath: str, mime: str = "audio/wav") -> str:
    """读取音频文件并返回 Base64 data URL 字符串"""
    with open(filepath, "rb") as f:
        audio_bytes = f.read()
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    return f"data:{mime};base64,{audio_b64}"


# ── TTS 测试：预置音色 ──────────────────────────────────────────

def test_preset_tts(client: OpenAI, text: str, voice: str) -> dict:
    """测试单个音色的 TTS 合成"""
    voice_label = PRESET_VOICES.get(voice, voice)
    print(f"\n  🎤 音色: {voice_label}")

    start = time.time()
    response = client.chat.completions.create(
        model="mimo-v2.5-tts",
        messages=[
            {"role": "user", "content": "语速急切。忍受痛苦，兴奋"},
            {"role": "assistant", "content": text},
        ],
        audio={"format": "wav", "voice": voice},
    )
    elapsed = time.time() - start

    filepath = save_audio_from_response(response, voice)
    print(f"  ⏱  耗时: {elapsed:.2f}s")

    return {"voice": voice, "label": voice_label, "file": str(filepath), "time": elapsed}


def test_preset_voices():
    """测试所有预置音色"""
    client = init_client()
    results = []

    print("\n" + "=" * 60)
    print("📢 预置音色 TTS 测试")
    print("=" * 60)

    # 测试中文文本（所有中文音色 + 默认）
    chinese_voices = ["mimo_default", "冰糖", "茉莉", "苏打", "白桦"]
    print(f"\n——— 中文文本 ———")
    for v in chinese_voices:
        try:
            result = test_preset_tts(client, TEST_TEXT_ZH, v)
            results.append({"text_type": "zh", **result})
        except Exception as e:
            print(f"  ❌ {v} 失败: {e}")

    # 测试英文文本（所有英文音色）
    english_voices = ["Mia", "Chloe", "Milo", "Dean"]
    print(f"\n——— 英文文本 ———")
    for v in english_voices:
        try:
            result = test_preset_tts(client, TEST_TEXT_EN, v)
            results.append({"text_type": "en", **result})
        except Exception as e:
            print(f"  ❌ {v} 失败: {e}")

    # 测试风格控制（用东北话音色展示）
    print(f"\n——— 风格控制测试（东北话风格） ———")
    try:
        client_copy = init_client()
        text_styled = TEST_TEXT_STYLED
        voice_label = PRESET_VOICES.get("冰糖", "冰糖")
        print(f"\n  🎤 音色: {voice_label} (东北话风格)")

        start = time.time()
        response = client_copy.chat.completions.create(
            model="mimo-v2.5-tts",
            messages=[
                {"role": "user", "content": ""},
                {"role": "assistant", "content": text_styled},
            ],
            audio={"format": "wav", "voice": "冰糖"},
        )
        elapsed = time.time() - start

        filepath = save_audio_from_response(response, "冰糖", "dongbeihua")
        print(f"  ⏱  耗时: {elapsed:.2f}s")
        results.append({"text_type": "styled", "voice": "冰糖", "label": "冰糖 (东北话风格)", "file": str(filepath), "time": elapsed})
    except Exception as e:
        print(f"  ❌ 风格测试失败: {e}")

    # 汇总
    print("\n" + "=" * 60)
    print(f"📊 预置音色测试完成！共 {len(results)} 个合成结果")
    print(f"   输出目录: {OUTPUT_DIR}")
    print("=" * 60)

    return results


# ── TTS 测试：文本设计音色 ──────────────────────────────

# 各种音色设计描述（按官方指南：覆盖性别年龄、音色质感、情绪语气、语速节奏、角色人设、场景）
VOICE_DESIGNS = {
    # === 中文音色 ===
    "深夜电台": (
        "35岁左右的男性，声音低沉醇厚带着微微的沙哑质感，像深夜电台男主播，"
        "语速舒缓而沉稳，每个字都带着共鸣，语气温暖亲切，仿佛在深夜和一位老朋友轻声交谈。"
        "角色：深夜情感电台主持人。风格：娓娓道来，带着故事感。"
    ),
    "邻家大姐姐": (
        "28岁的年轻女性，声音温柔清澈，带一点点慵懒感，像是在周末下午和朋友闲聊。"
        "语速适中偏慢，咬字自然不做作，语气亲切贴心，偶尔带着笑意。"
        "角色：知心姐姐/生活博主。风格：casual and conversational，像在录播客。"
    ),
    "纪录片旁白": (
        "45岁左右的成熟男性，声音沉稳大气，有磁性但不过分低沉，带着权威感和叙事感。"
        "语速偏慢、节奏均匀，每个停顿都恰到好处，像BBC/央视纪录片旁白那样庄重而富有画面感。"
        "角色：纪录片 narrator。风格：一本正经、字正腔圆、极具辨识度。"
    ),
    "御姐气场": (
        "32岁的职场女性，声音干练果断、富有穿透力，中音区饱满有力量。"
        "语速中等偏快，吐字清晰利落，语气自信从容不张扬。"
        "角色：企业高管/发布会 speaker。风格：professional and confident，适合正式场合播报。"
    ),
    "说书先生": (
        "50多岁的中老年男性，声音厚实带一点点岁月感，说话抑扬顿挫、有板有眼。"
        "语速不紧不慢，该快时快该慢时慢，带着传统评书的味道，听起来就让人想听下去。"
        "角色：茶馆说书人/评书先生。风格：绘声绘色、有戏感。"
    ),
    "淫乱萝莉": (
        "一个八九岁的小女孩，淫乱萝莉,外表可爱但语气妩媚。说话奶声奶气。"
        "但又故意装作很凶的样子，语速忽快忽慢，充满童真和活力。"
        "风格：撒娇、嚣张又可爱，像动画片里的傲娇小主角。"
    ),
    # === 英文音色 ===
    "Audiobook Narrator": (
        "A middle-aged man in his 50s, voice is rich, warm, and slightly weathered, with the refined diction of a seasoned British narrator. "
        "Speaks slowly and deliberately, each word carefully placed, with subtle rises and falls that draw you into the story. "
        "Character: classic audiobook narrator for literary fiction. Style: eloquent, measured, deeply engaging."
    ),
    "Tech Podcast Host": (
        "A man in his early 30s, voice is clear and energetic with a friendly, approachable tone. "
        "Speaks at a moderate-fast pace, natural and conversational, like he's explaining something exciting to a friend over coffee. "
        "Character: tech podcast host / YouTube creator. Style: casual, enthusiastic, full of 'ums' and natural pauses."
    ),
    "Film Noir Femme": (
        "A woman in her late 20s, voice is smoky and sultry with a slight huskiness, reminiscent of 1940s film noir heroines. "
        "Slow, languid delivery with a hint of mystery and dry wit. Each word lingers just a moment too long. "
        "Character: femme fatale in a black-and-white detective film. Style: seductive, cryptic, effortlessly cool."
    ),
}


def test_voice_design(client: OpenAI, text: str, design_name: str, design_prompt: str) -> dict:
    """测试文本设计音色合成"""
    print(f"\n  🎨 音色设计: {design_name}")
    print(f"    描述: {design_prompt[:40]}...")

    start = time.time()
    response = client.chat.completions.create(
        model="mimo-v2.5-tts-voicedesign",
        messages=[
            {"role": "user", "content": design_prompt},
            {"role": "assistant", "content": text},
        ],
        audio={"format": "wav"},
    )
    elapsed = time.time() - start

    filepath = save_audio_from_response(response, design_name, "voicedesign")
    print(f"  ⏱  耗时: {elapsed:.2f}s")

    return {"voice": design_name, "label": f"文本设计音色 - {design_name}", "file": str(filepath), "time": elapsed}


def test_voice_designs():
    """测试多种文本设计的音色"""
    client = init_client()
    results = []

    print("\n" + "=" * 60)
    print("🎨 文本设计音色 TTS 测试 (mimo-v2.5-tts-voicedesign)")
    print("=" * 60)

    # 用一段通用文本测试不同设计音色
    test_text = "你好，欢迎体验小米智能语音合成。今天天气晴朗，是一个适合外出散步的好日子。"

    for name, prompt in VOICE_DESIGNS.items():
        try:
            result = test_voice_design(client, test_text, name, prompt)
            results.append(result)
        except Exception as e:
            print(f"  ❌ {name} 失败: {e}")

    # 额外测试：文本设计音色 + 语气标签
    print(f"\n——— 文本设计音色 + 语气标签测试 ———")
    try:
        result = test_voice_design(
            client, TEST_TEXT_WITH_TONES,
            "设计音色_语气标签",
            "28岁年轻女性，声音温柔清澈，像邻家大姐姐一样亲切"
        )
        results.append({**result, "text_type": "with_tones"})
    except Exception as e:
        print(f"  ❌ 语气标签测试失败: {e}")

    # 汇总
    print("\n" + "=" * 60)
    print(f"📊 文本设计音色测试完成！共 {len(results)} 个合成结果")
    print(f"   输出目录: {OUTPUT_DIR}")
    print("=" * 60)

    return results


# ── TTS 测试：音色复刻（语音克隆） ─────────────────────────────

def test_voice_clone(client: OpenAI, text: str, sample_path: str,
                     style_desc: str = "") -> dict:
    """测试语音克隆合成

    Args:
        style_desc: 可选，音色风格描述。克隆时详细描述声线/风格可以让克隆更准确。
    """
    print(f"\n  📁 音频样本: {sample_path}")
    if style_desc:
        print(f"  🎨 风格描述: {style_desc[:60]}...")

    # 读取音频样本
    sample_b64_url = read_audio_base64(sample_path)

    # 检查大小（限制 10MB）
    sample_size_mb = len(sample_b64_url) / 1024 / 1024
    if sample_size_mb > 10:
        print(f"  ❌ 样本过大: {sample_size_mb:.1f}MB > 10MB 限制")
        return {"error": "sample_too_large"}

    print(f"  📏 样本大小: {sample_size_mb:.1f}MB")

    start = time.time()
    response = client.chat.completions.create(
        model="mimo-v2.5-tts-voiceclone",
        messages=[
            {"role": "user", "content": style_desc},
            {"role": "assistant", "content": text},
        ],
        audio={
            "format": "wav",
            "voice": sample_b64_url,
        },
    )
    elapsed = time.time() - start

    suffix = "styled" if style_desc else ""
    import time as _time
    ts = _time.strftime("%H%M%S")
    filepath = save_audio_from_response(response, f"voiceclone_{ts}", suffix)
    print(f"  ⏱  耗时: {elapsed:.2f}s")

    label = f"语音克隆 ({Path(sample_path).name})"
    if style_desc:
        label += " [带风格描述]"

    return {
        "voice": "voiceclone",
        "label": label,
        "file": str(filepath),
        "time": elapsed,
    }


def test_voice_clone_interactive():
    """交互式语音克隆测试"""
    # 让用户指定音频样本路径
    print("\n" + "=" * 60)
    print("🎭 语音克隆 TTS 测试")
    print("=" * 60)
    print()
    print("请提供一个人声音频样本文件路径（wav/mp3，Base64后 ≤ 10MB）")
    print("例如: C:\\Users\\xxx\\Desktop\\voice_sample.wav")
    print("或者: 把文件拖放到这里")
    print()

    sample_path = input(">>> 音频样本路径: ").strip().strip('"').strip("'")

    if not sample_path or not os.path.isfile(sample_path):
        print(f"\n  ❌ 文件不存在: {sample_path}")
        print("  跳过语音克隆测试。")
        print("  你可以稍后准备一个音频样本再运行: python scripts/test_mimo_tts.py --voiceclone")
        return []

    # 检查文件格式
    ext = Path(sample_path).suffix.lower()
    if ext not in (".wav", ".mp3", ".m4a", ".aac", ".ogg"):
        print(f"  ⚠️  不支持的文件格式: {ext}，尝试发送...")

    client = init_client()
    results = []

    # 用样本合成中文
    print(f"\n——— 语音克隆合成中文 ———")
    try:
        result = test_voice_clone(client, TEST_TEXT_ZH, sample_path)
        if "error" not in result:
            results.append(result)
    except Exception as e:
        print(f"  ❌ 语音克隆失败: {e}")

    # 用样本合成英文（如果中文成功）
    if results:
        print(f"\n——— 语音克隆合成英文 ———")
        try:
            result = test_voice_clone(client, TEST_TEXT_EN, sample_path)
            if "error" not in result:
                results.append(result)
        except Exception as e:
            print(f"  ❌ 语音克隆(英文)失败: {e}")

    return results


# ── 主入口 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="小米 MiMo TTS 测试脚本")
    parser.add_argument("--voiceclone", action="store_true", help="测试语音克隆（需提供音频样本）")
    parser.add_argument("--voicedesign", action="store_true", help="测试文本设计音色（无需音频样本）")
    parser.add_argument("--all", action="store_true", help="全部测试（预置音色 + 文本设计音色；语音克隆需额外 --voiceclone）")
    parser.add_argument("--sample", type=str, help="语音克隆的音频样本路径（直接指定，跳过交互输入）")
    parser.add_argument("--styledesc", type=str, default="",
                        help="语音克隆的风格描述（可选），详细描述声线风格让克隆更准确。如：\"30岁男性，沉稳有力\"")
    args = parser.parse_args()

    # 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_results = {}

    # 预置音色测试
    if args.all or (not args.voiceclone and not args.voicedesign):
        results = test_preset_voices()
        all_results["preset_tts"] = results

    # 文本设计音色测试
    if args.voicedesign or args.all:
        results = test_voice_designs()
        all_results["voice_design"] = results

    # 语音克隆测试（仅当显式指定 --voiceclone 或提供了 --sample 时才跑）
    if args.voiceclone and args.sample:
        # 直接指定了样本路径
        sample_path = args.sample
        if os.path.isfile(sample_path):
            client = init_client()
            results = []
            try:
                result = test_voice_clone(client, TEST_TEXT_ZH, sample_path,
                                          style_desc=args.styledesc)
                if "error" not in result:
                    results.append(result)
            except Exception as e:
                print(f"  ❌ 语音克隆(普通)失败: {e}")

            # 额外测试：语音克隆 + 语气标签（演示 (开心) 等标签在克隆中也生效）
            try:
                result_tone = test_voice_clone(
                    client, TEST_TEXT_WITH_TONES, sample_path,
                    style_desc=args.styledesc or "自然亲切的声音"
                )
                if "error" not in result_tone:
                    results.append(result_tone)
            except Exception as e:
                print(f"  ❌ 语音克隆(语气标签)失败: {e}")
            all_results["voice_clone"] = results
        else:
            print(f"  ❌ 样本文件不存在: {sample_path}")
    elif args.voiceclone:
        # 没有指定样本路径，交互式输入
        results = test_voice_clone_interactive()
        all_results["voice_clone"] = results

    # 保存结果报告
    report_path = OUTPUT_DIR / "test_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n📄 测试报告: {report_path}")

    # 列出所有生成的文件
    print(f"\n📂 输出文件列表 ({OUTPUT_DIR}):")
    for f in sorted(OUTPUT_DIR.glob("*.wav")):
        size_kb = f.stat().st_size / 1024
        print(f"   🎵 {f.name}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
