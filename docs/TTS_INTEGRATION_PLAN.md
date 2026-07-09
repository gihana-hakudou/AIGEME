# AIGEME 小米 MiMo TTS 集成方案

> 版本: v1.0
> 日期: 2026-07-09
> 状态: 设计稿

---

## 目录

1. [概述](#1-概述)
2. [后端模块架构](#2-后端模块架构)
3. [前端模块架构](#3-前端模块架构)
4. [前后端 API 设计](#4-前后端-api-设计)
5. [数据流图](#5-数据流图)
6. [动态提示词模板](#6-动态提示词模板)
7. [关键数据结构](#7-关键数据结构)
8. [实施计划](#8-实施计划)

---

## 1. 概述

### 1.1 目标

在 AIGEME 聊天系统中集成小米 MiMo TTS API，使 LLM 的输出能自动合成为语音播放。

### 1.2 核心链路

```
LLM 流式输出 → speak标签收集 → TTS合成请求 → 音频队列 → 音频播放
```

### 1.3 三种 TTS 模式

| 模式 | model | 特点 | 需要 |
|------|-------|------|------|
| 预置音色 | `mimo-v2.5-tts` | 固定音色列表，直接选择 | voice 参数 |
| 文本设计音色 | `mimo-v2.5-tts-voicedesign` | 自然语言描述生成音色 | 音色描述（user message） |
| 语音克隆 | `mimo-v2.5-tts-voiceclone` | 用音频样本克隆 | 音频 base64（voice 参数） |

---

## 2. 后端模块架构

### 2.1 模块总览

```
core/
├── tts/
│   ├── __init__.py          # 模块入口，导出 TTSManager
│   ├── config.py            # TTS 配置管理（读取/持久化）
│   ├── client.py            # MIMO TTS API 客户端封装（OpenAI 兼容格式）
│   ├── prompt_injector.py   # 动态提示词注入器
│   ├── speak_parser.py      # <speak> 标签解析器
│   ├── speak_queue.py       # Speak 标签队列调度器
│   └── routes.py            # TTS 相关 HTTP API 路由
frontend/
└── chat/
    ├── js/
    │   ├── tts.js           # TTS 前端模块（开关、播放控制、队列）
    │   └── ...              # 现有文件修改
    └── index.html           # 修改：添加 TTS 设置面板
```

### 2.2 模块详解

#### 2.2.1 `core/tts/config.py` — TTS 配置管理

负责 TTS 设置的读取、写入、缓存。复用现有的 settings.yaml + local.yaml 配置体系。

**TTS 配置按角色区分**，存储在角色目录的 `config.yaml` 中：

```yaml
# character/<角色名>/config.yaml 新增 tts 节点
tts:
  enabled: true                     # 该角色是否开启 TTS
  mode: "preset"                    # preset | voice_design | voice_clone
  voice: "冰糖"                    # 预置音色名称（默认冰糖）
  voice_design_prompt: ""           # 文本设计音色描述
  voice_clone_sample: null          # 语音克隆样本路径
  voice_clone_style_desc: ""        # 语音克隆风格描述
  tone: "自然温和"                 # 默认语气指导
```

**全局配置**（local.yaml）只存 API Key：
```yaml
# .AIGEME/local.yaml
tts:
  api_key: "sk-..."
```

**核心接口**：

```python
class TTSConfig:
    @staticmethod
    def load(character: str) -> dict:
        """加载指定角色的 TTS 配置（合并全局 api_key）"""
        ...
    
    @staticmethod
    def save(character: str, overrides: dict) -> None:
        """保存角色 TTS 配置到 character/<角色>/config.yaml"""
        ...
    
    @staticmethod
    def get_cache_dir(character: str) -> Path:
        """返回 TTS 音频缓存目录
        .AIGEME/.data/local/<角色名>/tts-wav/
        自动创建目录
        """
        ...
```

**TTS 音频缓存路径**：
```
.AIGEME/.data/local/<角色名>/tts-wav/
├── <timestamp>_<turn_id>.wav     # 整轮对话拼接后的完整音频
└── ...
```
- 每轮对话的多个 `<speak>` 标签合成后，**合并成一段 WAV 再保存**
- 文件名用 `{timestamp}_{turn_id}.wav`
- 缓存用于重复播放（重播时不重新合成）

#### 2.2.2 `core/tts/client.py` — MIMO TTS API 客户端

基于现有 `scripts/test_mimo_tts.py` 的测试经验，封装 TTS 合成 API。

```python
class MimoTTSClient:
    def __init__(self, api_key: str): ...
    
    async def synthesize(
        self,
        text: str,
        mode: str = "preset",       # preset | voice_design | voice_clone
        voice: str = "mimo_default",
        tone_guide: str = "",
        voice_design_prompt: str = "",
        voice_clone_sample_b64: str | None = None,
        voice_clone_style: str = "",
    ) -> TTSResult: ...
    
    async def test_synthesize(
        self,
        text: str,
        config: dict,               # 完整的 TTS 配置
    ) -> TTSResult: ...
```

**TTSResult:**

```python
@dataclass
class TTSResult:
    audio_data: bytes        # WAV 原始音频
    duration_ms: int         # 音频时长（毫秒）
    format: str = "wav"      # 音频格式
```

**API 调用映射（基于测试脚本）：**

| 模式 | model | messages | audio |
|------|-------|----------|-------|
| preset | `mimo-v2.5-tts` | user=tone_guide, assistant=text | `{"format":"wav","voice":voice}` |
| voice_design | `mimo-v2.5-tts-voicedesign` | user=design_prompt, assistant=text | `{"format":"wav"}` |
| voice_clone | `mimo-v2.5-tts-voiceclone` | user=style_desc, assistant=text | `{"format":"wav","voice":sample_b64}` |

#### 2.2.3 `core/tts/prompt_injector.py` — 动态提示词注入器

在 LLM 的 system prompt 末尾注入 TTS 格式指令，指导 LLM 输出 `<speak>` 标签。

**注入时机：** `PromptAssembler.build_system_prompt()` 中，在固定部分末尾追加 TTS 格式指令。

**注入逻辑：**
- 仅在 TTS 开启时注入
- 根据当前 TTS mode 提供不同的指导

```python
class TTSPromptInjector:
    @staticmethod
    def build_tts_instruction(tts_config: dict) -> str:
        """构建 TTS 格式指导文本"""
        ...
```

#### 2.2.4 `core/tts/speak_parser.py` — 标签解析器

从 LLM 流式输出中实时解析 `<speak tone="X">` 标签，提取语气和文本。

```python
class SpeakParser:
    """
    流式解析器：解析 <speak tone="语气">文本</speak>
    
    职责：
    1. 实时收集流式文本中的 <speak> 标签
    2. 提取 tone 属性（语气）和标签内文本
    3. 生成纯净文本（剥离所有标签，用于持久化和前端渲染）
    """
    
    # speak 标签正则
    SPEAK_START = re.compile(r'<speak(?:\s+tone="([^"]*)")?\s*>')
    SPEAK_END = re.compile(r'</speak>')
    
    def __init__(self):
        self._buffer = ""
        self._speak_texts: list[str] = []  # 已完成的 speak 文本（含 tone 前缀，供 TTS 合成）
        self._clean_buffer = ""            # 剥离标签后的纯净文本（供渲染和持久化）
        ... 
    
    def feed(self, delta: str) -> list[CompletedSpeak]:
        """
        输入流式片段，返回已完成的 Speak 标签列表。
        
        TTS 合成时，tone 映射为 (语气) 前缀附加到文本前发送给 MIMO API：
          tone="兴奋" → 发送 "(兴奋)今天天气真好！"
        """
        ...
    
    def get_clean_text(self) -> str:
        """获取已剥离所有标签的纯净文本（用于持久化和前端渲染）"""
        ...
    
    @staticmethod
    def strip_tags(text: str) -> str:
        """移除所有 <speak ...> 和 </speak> 标签，保留纯文本
        用于：持久化对话存储、回传对话历史列表
        """
        return SPEAK_TAG_PATTERN.sub('', text)
    
    @staticmethod
    def tone_to_prefix(tone: str) -> str:
        """将 speak 标签的 tone 属性转为 MIMO API 接受的 (语气) 前缀
        例如: "兴奋" → "(兴奋)" , "开心+御姐音" → "(开心+御姐音)"
        多个维度用 + 连接，如 "(悲伤+沙哑)"
        """
        return f"({tone})" if tone else ""
```

**CompletedSpeak 结构：**

```python
@dataclass
class CompletedSpeak:
    text: str          # 标签内纯净文本（无标签、无 tone 前缀）
    tts_text: str      # 发送给 TTS API 的文本（含 (语气) 前缀）
    tone: str          # 语气（从 tone 属性提取，如 "兴奋"）
    index: int         # 标签序号（用于顺序保证）
```

> **合成映射规则**：LLM 输出 `<speak tone="兴奋">今天天气真好！</speak>` 
> → Parser 提取 tone="兴奋", text="今天天气真好！"
> → 合成时发送 `tts_text = "(兴奋)今天天气真好！"` 给 MIMO API

#### 2.2.5 `core/tts/speak_queue.py` — 队列调度器

管理 speak 标签的 TTS 合成与播放调度。

```python
class SpeakQueue:
    """
    Speak 标签队列调度器。
    
    职责：
    1. 接收 parser 产出的 CompletedSpeak
    2. 按 index 顺序调度 TTS 合成
    3. 合成完成后通知前端播放（通过 WS Block）
    
    关键行为：
    - 顺序保证：即使后一个标签先合成完，也等待前一个播放完再播放
    - 中断：新一轮 say 时清空队列，停止正在合成的请求
    - 并发：最多 1 个正在合成的请求 + N 个等待合成的请求
    """
    
    def __init__(self, tts_client: MimoTTSClient, send_block: Callable):
        self._queue: asyncio.Queue[QueueItem] = ...
        self._current_task: asyncio.Task | None = None
        self._next_index: int = 0         # 下一个应播放的 index
        self._pending: dict[int, bytes] = {}  # 已合成但还未轮到播放的音频
        self._tts_client = tts_client
        self._send_block = send_block
    
    async def enqueue(self, speak: CompletedSpeak) -> None:
        """添加一个 speak 到队列"""
        ...
    
    async def flush(self) -> None:
        """清空队列（新一轮输出时调用）"""
        ...
    
    async def _process_queue(self) -> None:
        """后台任务：按顺序处理 TTS 合成 → 播放"""
        ...
```

**队列调度逻辑：**

```
enqueue(speak_1)       → 立即合成 TTS_1 → send_block(audio_1) → 等待播放完成
enqueue(speak_2)       → 排队，等待前一个
enqueue(speak_3)       → 排队
                           ↓
TTS_1 合成完成         → send_block(audio_1) → 等待前端播放完成通知
                           ↓
TTS_2 合成完成（快）   → 放入 pending[2]
                           ↓
前端播放完成            → 拉取 TTS_2 → send_block(audio_2)
                           ↓
                          ...
```

**新增：音频合并（一轮对话的多段合成）**

一轮对话中可能包含多个 `<speak>` 标签（如不同语气的段落）。流式播放时每个标签独立下发 audio block，但**整轮对话结束后**需要将所有分段合并为一个完整 WAV 文件存入缓存。

```python
class AudioMerger:
    """
    音频合并器：将多个 WAV 音频段合并为一个完整的 WAV 文件。
    
    关键处理：
    1. 爆音消除（Pop/Click 消除）— 在拼接点应用短交叉淡入淡出
    2. 采样率统一 — 所有分段采样率必须一致（MIMO 固定 24kHz）
    3. 位深统一 — 16-bit PCM
    """
    
    @staticmethod
    def merge(audio_chunks: list[bytes], crossfade_ms: int = 5) -> bytes:
        """
        合并多个 WAV 音频块。
        
        爆音处理策略：
        - 在每个拼接点前 5ms 做淡出（线性）
        - 在每个拼接点后 5ms 做淡入（线性）
        - 拼接处重叠 5ms 并叠加
        - 最终输出完整的 16-bit PCM WAV
        
        WAV 格式假定：
        - 采样率: 24000 Hz
        - 位深: 16-bit signed
        - 通道: 单声道 (mono)
        - 无压缩 (PCM)
        """
        ...
    
    @staticmethod
    def save_turn_audio(character: str, turn_id: str,
                        merged_wav: bytes) -> Path:
        """
        将合并后的 WAV 保存到缓存目录。
        路径: .AIGEME/.data/local/<角色名>/tts-wav/<timestamp>_<turn_id>.wav
        """
        ...
```

**缓存与重播逻辑**：
```
新轮次开始 → SpeakQueue 清空
所有 speak 合成完毕 → AudioMerger.merge(all_chunks)
  → 保存到角色缓存目录
  → 标记 turn_id 的缓存可用

用户点击"重播" → 检查缓存是否存在
  → 存在：直接读取缓存文件播放（不重新合成）
  → 不存在：重新走 TTS 合成流程
```

**爆音处理示意**：
```
拼接前:
  chunk_1: [...样本数据...]                chunk_2: [...样本数据...]
                              ↓
拼接中（5ms 交叉淡入淡出）:
  chunk_1: [....\ 逐渐减小 /  ...]  
  chunk_2: [...  / 逐渐增大 \....]
                              ↓
拼接后:
  merged: [.....\________/....]   ← 平滑过渡，无爆音
```

#### 2.2.6 `core/tts/routes.py` — TTS HTTP API

```python
router = APIRouter(prefix="/api/tts", tags=["tts"])

@router.get("/config")
async def get_tts_config() -> dict: ...

@router.put("/config")
async def update_tts_config(config: dict) -> dict: ...

@router.get("/voices")
async def list_preset_voices() -> dict: ...
# 返回：{"voices": {"mimo_default": "MiMo-默认", "冰糖": "冰糖 (中文女声)", ...}}

@router.post("/test")
async def test_tts(request: Request, config: dict) -> dict: ...
# 用配置进行一次测试合成，返回 audio_data base64
```

### 2.3 Hook 点：流式输出集成

在 `core/raact_loop/loop.py` 的 `raact_stream()` 中：

**修改点（约 3 处）：**

1. **初始化时**：检查 TTS 是否开启。如果开启，创建 `SpeakParser` + `SpeakQueue`
2. **speech block 输出时**：将 delta 同时送入 `SpeakParser.feed()`，把产出的 `CompletedSpeak` enqueue 到 `SpeakQueue`
3. **turn_end 时**：关闭 parser，等待队列中所有合成完成（或 flush）

```
# 伪代码修改示意

async def raact_stream(self, ...):
    tts_config = TTSConfig.load()
    tts_enabled = tts_config.get("enabled", False)
    
    parser = SpeakParser() if tts_enabled else None
    queue = SpeakQueue(mimo_client, send_block) if tts_enabled else None
    
    async def enhanced_send_block(block: Block):
        if tts_enabled and block.block_type == "speech" and not block.is_final:
            # 将流式文本喂给 parser
            completed_list = parser.feed(block.delta)
            for completed in completed_list:
                await queue.enqueue(completed)
        
        # 仍然正常发送 speech block（文本渲染不受影响）
        await original_send_block(block)
    
    # 使用 enhanced_send_block 替代原始 send_block
    ...
```

---

## 3. 前端模块架构

### 3.1 `frontend/chat/js/tts.js` — 前端 TTS 模块

```javascript
/**
 * TTS 前端模块
 * 
 * 职责：
 * - 管理 TTS 开关状态
 * - AudioContext 管理 + 音频播放
 * - 自动播放控制
 * - 播放列表队列
 * - 中断逻辑
 */
const TTSPlayer = {
    // ── 状态 ──
    enabled: false,                // TTS 开关
    isPlaying: false,              // 是否正在播放
    audioQueue: [],                // [{id, audioData, index}]
    currentAudio: null,            // 当前播放的 Audio 对象
    currentSource: null,           // AudioBufferSourceNode
    audioContext: null,            // AudioContext (懒初始化)
    playIndex: 0,                  // 下一个应播放的序号
    turnId: 0,                     // 当前轮次 ID（用于中断判定）
    
    // ── 初始化 ──
    init: function() { ... },
    
    // ── 开关控制 ──
    toggle: function(enabled) { ... },
    
    // ── 音频播放 ──
    play: function(audioData, index) {
        // 1. 检查 index 是否 >= playIndex，否则丢弃（过期）
        // 2. 如果 index == playIndex，立即播放
        // 3. 如果 index > playIndex，放入 audioQueue 等待
        // 4. 播放结束后，playIndex++，检查 queue 中下一个
    },
    
    // ── 中断 ──
    interrupt: function(newTurnId) {
        // 1. 停止当前播放
        // 2. 清空 audioQueue
        // 3. 重置 playIndex
        // 4. 更新 turnId
    },
    
    // ── 播放控制 ──
    stop: function() { ... },
    resume: function() { ... },
    pause: function() { ... },
    
    // ── 内部 ──
    _decodeAudio: async function(audioData) { ... },
    _playNext: function() { ... },
    _onEnded: function() { ... },
};
```

### 3.2 核心逻辑

#### 3.2.1 中断机制

```
用户发送新消息（sendMessage）
  → TTSPlayer.interrupt(newTurnId)
  → 停止当前播放
  → 清空队列
  → 重置序号
  → 后续 audio block 的 index < playIndex → 全部丢弃
```

#### 3.2.2 自动播放

```
收到 speech block（流式开始）
  → TTSPlayer 检查 enabled
  → 异步等待 audio block 到来
  → audio block 到达 → decode → play
  → 播放完成后检查队列中是否有下一个
```

#### 3.2.3 播放队列

队列按 index **严格递增**。每个 audio block 携带一个 `index` 字段。

```
audio block { audio_data: ..., index: 2 }
  → playIndex == 2 → 立即播放
  → playIndex == 0 → 放入 queue: {2: audioData}
  
当前播放 index=0
  → 结束后 playIndex=1
  → 检查 queue[1] 是否存在
  → 存在则播放，不存在则等待
```

### 3.3 HTML 修改

**index.html** 修改点：

1. **聊天选项区域**（chat-options）：现有的语音 checkbox 绑定 TTS 开关
```html
<label class="opt">
  <input type="checkbox" id="tts-toggle"> 🔊 TTS 语音
</label>
```

2. **设置页面新增 TTS 区块**：
```html
<div class="sblock" id="tts-settings-block">
  <h3>🎤 TTS 语音设置（小米 MiMo）</h3>
  <div class="srow">
    <label>API Key</label>
    <input type="password" class="sinput" id="tts-api-key" placeholder="sk-...">
  </div>
  <div class="srow">
    <label>语音模式</label>
    <select id="tts-mode" class="sselect">
      <option value="preset">预置音色</option>
      <option value="voice_design">文本设计音色</option>
      <option value="voice_clone">语音克隆</option>
    </select>
  </div>
  <!-- 预置音色选择（preset 模式显示） -->
  <div class="srow" id="tts-voice-row">
    <label>音色</label>
    <select id="tts-voice" class="sselect"></select>
  </div>
  <!-- 文本设计音色描述（voice_design 模式显示） -->
  <div class="srow" id="tts-design-row" style="display:none;">
    <label>音色描述</label>
    <textarea id="tts-design-prompt" class="sinput" rows="3"
      placeholder="例：35岁男性，声音低沉醇厚..."></textarea>
  </div>
  <!-- 语音克隆样本（voice_clone 模式显示） -->
  <div class="srow" id="tts-clone-row" style="display:none;">
    <label>音频样本</label>
    <input type="file" id="tts-clone-sample" accept="audio/*">
    <input class="sinput" id="tts-clone-style" placeholder="风格描述（可选）">
  </div>
  <div class="srow">
    <label>语气指导（默认）</label>
    <input class="sinput" id="tts-tone" placeholder="例：自然温和、兴奋、严肃">
  </div>
  <div class="srow" style="justify-content:center;">
    <button id="btn-test-tts" class="cute-btn">🔊 测试语音</button>
  </div>
</div>
```

3. **消息气泡中的播放按钮**：每条 AI 消息旁加播放/停止图标

### 3.4 Block 协议扩展

新增 Block 类型：

```python
# core/protocols/blocks.py 新增
BlockType = Literal[
    ...,
    "audio",              # TTS 音频数据
    "audio_play_end",     # 前端通知后端音频播放结束
    "tts_state",          # TTS 状态同步
]
```

**audio block:**

```python
Block(
    block_type="audio",
    delta=base64_audio_data,   # WAV base64
    metadata={
        "index": 0,            # 播放序号（顺序保证）
        "format": "wav",
        "duration_ms": 3000,
    }
)
```

**audio_play_end block (客户端→服务端):**

```javascript
{ type: "audio_play_end", index: 0 }
```

**tts_state block:**

```python
Block(
    block_type="tts_state",
    delta="",
    metadata={
        "enabled": True,
        "is_playing": False,
        "queue_length": 0,
    }
)
```

---

## 4. 前后端 API 设计

### 4.1 HTTP API

| 方法 | 路径 | 说明 | 请求体 | 响应 |
|------|------|------|--------|------|
| GET | `/api/tts/config` | 获取 TTS 配置 | — | `{enabled, mode, voice, ...}` |
| PUT | `/api/tts/config` | 更新 TTS 配置 | `{mode, voice, ...}` | `{status, message}` |
| GET | `/api/tts/voices` | 获取预置音色列表 | — | `{voices: {id: name, ...}}` |
| POST | `/api/tts/test` | 测试 TTS 合成 | `{mode, voice, text}` | `{audio_data(base64), duration_ms}` |

### 4.2 WebSocket Block 扩展

| Block 方向 | Block Type | 说明 |
|-----------|-----------|------|
| 服务端→客户端 | `audio` | 下发 TTS 合成音频 |
| 客户端→服务端 | `audio_play_end` | 通知播放完成 |
| 服务端→客户端 | `tts_state` | TTS 状态同步 |

### 4.3 WebSocket 消息扩展

客户端消息新增字段：

```javascript
// user_message 中新增
{
    type: "user_message",
    content: "...",
    tts_enabled: true,      // 携带当前 TTS 开关状态
}
```

---

## 5. 数据流图

### 5.1 完整链路

```
┌─────────────────────────────────────────────────────────────────────┐
│  LLM 流式输出                                                       │
│                                                                     │
│  ...<speak>(兴奋)今天天气真好！</speak>                         │
│  <speak>(严肃)不过下午可能要下雨。</speak>...                    │
└──────────────────────┬──────────────────────────────────────────────┘
                       │ delta: "...<speak>(兴奋)今天天气真好！"
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  SpeakParser.feed(delta)                                             │
│                                                                      │
│  输入: "<speak>(兴奋)今天天气真好！</speak>..."                        │
│  输出: [CompletedSpeak(text="今天天气真好！", raw_text="(兴奋)今天天气真好！", tone="兴奋", index=0)] │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ item = CompletedSpeak(text="今天天气真好！", ...)
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  SpeakQueue.enqueue(item)                                            │
│                                                                      │
│  启动 TTS 合成任务（后台）：                                          │
│    client.synthesize(raw_text="(兴奋)今天天气真好！", mode, voice)   │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ async: TTS API 请求
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  MimoTTSClient.synthesize() → base64 WAV                             │
│                                                                      │
│  model="mimo-v2.5-tts"                                              │
│  messages=[{role:user, content:tone_guide},{role:assistant, content:"(兴奋)今天天气真好！"}]│
│  audio={format:"wav", voice:"冰糖"}                                 │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ TTSResult(audio_data=..., duration_ms=2500)
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  SpeakQueue 调度                                                      │
│                                                                      │
│  1. 等待前一个 index 播放完成                                         │
│  2. send_block(audio_block {delta: base64, metadata: {index: 0}})   │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ WebSocket → audio block
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  前端 TTSPlayer                                                       │
│                                                                      │
│  1. 收到 audio block                                                 │
│  2. 检查 index == playIndex → 立即播放                               │
│  3. AudioContext.decodeAudioData() → AudioBufferSourceNode.start()   │
│  4. 播放结束 → playIndex++ → 检查 queue → 播放下一个                  │
│  5. 发送 audio_play_end(index) 给服务端                               │
└──────────────────────────────────────────────────────────────────────┘
```

### 5.2 中断流程

```
用户发送新消息
  │
  ├── 前端:
  │     TTSPlayer.interrupt(newTurnId)
  │       ├── currentAudio.stop()           # 停止当前播放
  │       ├── audioQueue = []               # 清空队列
  │       ├── playIndex = 0                 # 重置序号
  │       └── turnId = newTurnId
  │
  └── 后端 (通过 WS cancel):
        SpeakQueue.flush()
          ├── 取消当前正在合成的请求（如果有）
          ├── 清空等待队列
          └── 重置 next_index = 0
```

### 5.3 同时输出文本 + 语音

```
LLM 输出: "<speak>今天天气真好！</speak>你觉得呢？"
                          │                      │
                          ▼                      ▼
                    SpeakParser 解析      BlockRenderer 渲染
                          │                      
                          ▼                      
                    "今天天气真好！"         chat-text 显示: "今天天气真好！你觉得呢？"
                          │
                          ▼
                    TTS 合成 + 播放
                    
    注意：speak 标签外的文本不参与 TTS 合成，但正常显示在聊天界面。
```

---

## 6. 动态提示词模板

### 6.1 System Prompt 注入内容

在 `PromptAssembler.build_system_prompt()` 末尾追加：

```markdown
## 语音输出格式指导

当 TTS 语音功能开启时，请遵循以下格式：

1. **需要朗读的内容**使用 `<speak>` 标签包裹，tone 属性指定语气：
   ```
   <speak tone="语气描述">需要朗读的文本</speak>
   ```

2. **tone 属性**支持丰富的风格控制，可以用 `+` 组合多个维度，如 `tone="开心+御姐音"` 、 `tone="悲伤+沙哑"`：

   **基础情绪**：开心、悲伤、愤怒、恐惧、惊讶、兴奋、委屈、平静、冷漠

   **复合情绪**：怅然、欣慰、无奈、愧疚、释然、嫉妒、厌倦、忐忑、动情

   **整体语调**：温柔、高冷、活泼、严肃、慵懒、俏皮、深沉、干练、凌厉

   **音色质感**：磁性、醇厚、清亮、空灵、稚嫩、苍老、甜美、沙哑、醇雅

   **人设腔调**：夹子音、御姐音、正太音、大叔音、台湾腔

   **方言**：东北话、四川话、河南话、粤语

   **角色扮演**：孙悟空、林黛玉

   **特殊模式**：唱歌（文本作为歌词唱出来）

   也可用自然语言描述，如 `tone="轻声细语，像在哄小朋友睡觉"`

3. **不需要朗读的内容**必须放在 speak 标签外，包括但不限于：
   - 冗长的代码块（代码不适合朗读）
   - 网页链接/URL
   - 表情/立绘标签（如 `<tachie-e>`, `[图片]`）
   - 动作描述（如 *她笑了笑*）
   - 纯数字/表格数据

4. **⚠️ 语言限制**：`<speak>` 标签**仅支持中文和英文**。日语、韩语、法语等其他语言的文本不要用 speak 标签包裹。

5. **示例**：
   ```
   <speak tone="兴奋">今天天气真好！我们去散步吧。</speak>
   具体的天气预报数据：25°C，湿度60%，详见 https://example.com/weather
   <speak tone="开心">The weather is great today!</speak>
   以下是日语原文：こんにちは、元気ですか？
   <tachie-e>happy</tachie-e>
   ```

6. **多段标注**：一段话需要不同语气时，拆成多个 speak 标签。

7. **纯非语音内容**：整段都不需要朗读时，不加任何 speak 标签。
```

### 6.2 条件注入

仅在 TTS `enabled=true` 时注入。通过 `PromptAssembler.build_variable_content()` 返回：

```python
# core/tts/prompt_injector.py

def build_variable_tts_reminder(tts_config: dict) -> str | None:
    if not tts_config.get("enabled"):
        return None
    tone = tts_config.get("tone", "自然温和")
    return (
        "（当前语音已开启，请使用 <speak> 标签标注需要朗读的内容。"
        f"默认语气: {tone}）"
    )
```

然后在 `PromptAssembler.build_variable_content()` 中追加：

```python
tts_reminder = TTSPromptInjector.build_variable_tts_reminder(tts_config)
if tts_reminder:
    parts.append(tts_reminder)
```

### 6.3 标签剥离规则（消息生命周期）

项目消息架构分为**内存列表**和**持久化**两部分。`<speak>` 标签只在流式处理阶段存在，一旦消息完成立即剥离。

#### 剥离函数

```python
def strip_tts_tags(text: str) -> str:
    """移除所有 <speak ...> 和 </speak> 标签，保留纯文本"""
    import re
    text = re.sub(r'<speak\s[^>]*>', '', text)   # <speak tone="X">
    text = re.sub(r'</speak>', '', text)          # </speak>
    text = re.sub(r'<speak>', '', text)           # <speak>
    return text.strip()
```

#### 消息全生命周期中的标签处理

```
                          LLM 流式输出
                              │
                    ┌─────────┴─────────┐
                    │                    │
            SpeakParser.feed()    前端流式渲染
            解析 <speak> 标签       (剥离标签后显示)
                    │
         ┌──────────┴──────────┐
         │                     │
    TTS 合成队列         消息流式完成
    (含 tone 前缀)           │
                              │ strip_tts_tags()
                              ▼
                     ┌────────────────┐
                     │  内存消息列表    │ ← 存 clean_text（无标签）
                     └───────┬────────┘
                             │
               ┌─────────────┼─────────────┐
               │             │             │
               ▼             ▼             ▼
        回传 LLM 上下文   前端渲染历史    持久化落盘
        (clean_text)     (clean_text)   (clean_text)
```

#### 各阶段处理对照

| 阶段 | 存储位置 | 内容 | 是否含标签 |
|------|---------|------|-----------|
| 流式处理中 | SpeakParser._buffer | 原始 delta 片段 | ✅ 含标签，供实时解析 |
| 消息流式完成 | → 剥离后写入**内存列表** | clean_text | ❌ 已剥离 |
| 持久化落盘 | 从内存列表直接写入磁盘文件 | clean_text | ❌ 无标签 |
| 回传 LLM 上下文 | 从内存列表读取发送 | clean_text | ❌ LLM 不再看到自己输出的标签 |
| 恢复对话加载 | 从磁盘加载 → 内存列表 | clean_text | ❌ 全程无标签 |

> 💡 **核心原则**：`<speak>` 标签是流式阶段的临时标记，一条消息的流式完成后立即剥离。剥离后的 clean_text 进入内存列表和持久化，之后所有环节（LLM 回传、前端渲染、历史加载）都使用 clean_text。
```

---

## 7. 关键数据结构

### 7.1 后端数据结构

```python
# ── SpeakParser 内部状态 ──
@dataclass
class CompletedSpeak:
    """一个已经完成的 <speak> 标签"""
    text: str          # 标签内文本
    tone: str          # tone 属性
    index: int         # 序号

# ── SpeakQueue 内部状态 ──
@dataclass
class QueueItem:
    """队列中的待处理项"""
    speak: CompletedSpeak
    state: Literal["pending", "synthesizing", "synthesized", "playing", "done"]
    audio_data: bytes | None = None

# ── TTS 合成结果 ──
@dataclass
class TTSResult:
    audio_data: bytes
    duration_ms: int
    format: str = "wav"
```

### 7.2 前端数据结构

```javascript
// ── TTSPlayer 状态 ──
const TTSPlayerState = {
    enabled: false,            // Boolean - TTS 开关
    isPlaying: false,          // Boolean - 正在播放
    audioQueue: [],            // [{index: Number, audioData: ArrayBuffer}]
    currentAudio: null,        // AudioBufferSourceNode | null
    audioContext: null,        // AudioContext | null
    playIndex: 0,              // Number - 下一个应播放的序号
    turnId: 0,                 // Number - 当前轮次 ID
    
    // 配置缓存
    config: {
        mode: 'preset',        // 'preset' | 'voice_design' | 'voice_clone'
        voice: '冰糖',
        tone: '自然温和',
    }
};

// ── audio block 格式 ──
const AudioBlock = {
    type: 'block',
    block_type: 'audio',
    delta: '<base64-wav-data>',
    metadata: {
        index: 0,              // 播放序号
        format: 'wav',
        duration_ms: 2500,
    }
};
```

### 7.3 配置持久化

```yaml
# .AIGEME/local.yaml 新增
tts:
  enabled: false
  api_key: "sk-ckmkntxlz51r88im8wlc2qzxuc2j306amadqy43nhltwp4uy"
  mode: "preset"
  voice: "冰糖"
  voice_design_prompt: ""
  voice_clone_sample: null
  voice_clone_style_desc: ""
  tone: "自然温和"
```

---

## 8. 实施计划

### Phase 1: 基础设施（预计 1 天）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 1.1 | 创建 `core/tts/__init__.py` | 新文件 | 模块入口 |
| 1.2 | 实现 `core/tts/config.py` | 新文件 | 配置读写 + 缓存 |
| 1.3 | 实现 `core/tts/client.py` | 新文件 | MIMO API 客户端 |
| 1.4 | 实现 `core/tts/routes.py` | 新文件 | TTS HTTP API |
| 1.5 | 在 `core/main.py` 注册路由 | 修改文件 | `app.include_router(tts_router)` |

### Phase 2: 核心逻辑（预计 1.5 天）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 2.1 | 实现 `core/tts/speak_parser.py` | 新文件 | 流式标签解析 |
| 2.2 | 实现 `core/tts/speak_queue.py` | 新文件 | 队列调度 |
| 2.3 | 实现 `core/tts/prompt_injector.py` | 新文件 | 提示词注入 |
| 2.4 | 修改 `core/raact_loop/loop.py` | 修改文件 | Hook speak parser + queue |
| 2.5 | 扩展 `core/protocols/blocks.py` | 修改文件 | 添加 audio/audio_play_end 类型 |

### Phase 3: 前端（预计 1.5 天）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 3.1 | 创建 `frontend/chat/js/tts.js` | 新文件 | TTS 播放模块 |
| 3.2 | 修改 `frontend/chat/index.html` | 修改文件 | TTS 设置面板 + 控制按钮 |
| 3.3 | 修改 `frontend/chat/js/blocks.js` | 修改文件 | 添加 audio block handler |
| 3.4 | 修改 `frontend/chat/js/state.js` | 修改文件 | 添加 TTS 状态字段 |
| 3.5 | 修改 `frontend/chat/js/app.js` | 修改文件 | 绑定 TTS 开关 + 中断 |

### Phase 3: 集成测试（预计 0.5 天）

| # | 任务 | 说明 |
|---|------|------|
| 4.1 | 端到端流程测试 | 发送消息 → LLM 输出 → speak 标签 → TTS 合成 → 播放 |
| 4.2 | 中断测试 | 播放过程中发送新消息 → 正确中断 |
| 4.3 | 顺序保证测试 | 多个 speak 标签，合成时间不同 → 播放顺序正确 |
| 4.4 | 三种模式测试 | preset / voice_design / voice_clone 分别测试 |

### 风险与注意事项

1. **API Key 安全**：TTS API Key 不应存储在 settings.yaml（版本化），应类似 LLM API Key 存入 local.yaml 或环境变量
2. **音频格式**：MIMO 返回 WAV 格式，前端 AudioContext 原生支持
3. **并发控制**：TTS 合成是异步 HTTP 调用，队列调度必须处理竞态条件
4. **错误处理**：TTS 合成失败时，不应阻塞文本输出；播放失败应 fallback 静默
5. **性能**：WAV 音频 base64 通过 WebSocket 传输，注意单次不宜过大（MIMO 限制 10MB base64）
