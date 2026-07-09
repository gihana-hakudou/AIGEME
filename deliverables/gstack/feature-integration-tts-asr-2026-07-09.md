# AIGEME 小米 MiMo TTS 集成方案

**日期**：2026-07-09
**场景**：全流程交付（产品评审 + 设计审查）
**参与成员**：产品评审员 + 设计师

---

## 📌 TL;DR

- **整体结论**：🟢 方案已就绪，可进入实施阶段
- **方案规模**：6 个后端模块 + 5 个前端文件修改 + 1 个 UI 原型
- **估算工时**：4 个 Phase，约 4 天
- **核心思路**：LLM 流式输出 → `<speak>` 标签解析 → 按序 TTS 合成 → 严格队列播放
- 动态提示词仅在 TTS 开启时注入，不影响正常对话

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Go |
| 关键行动项 | 15 条（分 4 个 Phase） |
| 建议负责人 | Gu（主理人） |
| 前置条件 | 确认 API Key 有效性，确认 local.yaml 配置体系 |

---

## 1. 各成员核心结论

### 🔍 产品评审员

**核心判断**：小米 MIMO TTS 的三种模式（预置音色/文本设计/语音克隆）都已在测试中验证可用，集成方案架构清晰，核心链路由 5 个环节组成：LLM 流式输出 → SpeakParser 标签解析 → SpeakQueue 队列调度 → MimoTTSClient 合成 → 前端 TTSPlayer 播放。

**关键建议**：
- 采用 `local.yaml` 存储 TTS 配置（API Key 不走版本控制）
- `speak` 标签外的文本正常显示在聊天界面但跳过 TTS 合成
- 并发控制：TTS 合成每次最多 1 个请求，队列按 index 严格调度
- 中断机制：用户发新消息时，前端立即清空队列 + 停止播放，后端 flush 取消合成

完整架构方案见 `docs/TTS_INTEGRATION_PLAN.md`

### 🎨 设计师

**核心判断**：前端 UI 原型覆盖了 4 大模块——语音全局开关、TTS 设置面板、消息播放控制器、播放队列状态栏。紫调主色 `#5B5FC7`，支持暗色模式，响应式布局。

**关键建议**：
- 侧边栏可折叠展开，不影响聊天主界面
- 消息气泡播放按钮显示 `<speak>` 段数标注，用户可感知语音内容
- 播放中状态用脉冲圆点动画提示
- 关闭语音开关后所有控件隐藏，UX 干净

UI 原型见 `deliverables/gstack/tts-ui-prototype.html`

---

## 2. 综合方案总览

### 后端模块架构

```
core/tts/
├── __init__.py              # 模块入口，导出 TTSManager
├── config.py                # TTS 配置管理（按角色，存 character/<角色>/config.yaml）
├── client.py                # MIMO API 封装（OpenAI 兼容格式）
├── prompt_injector.py       # 动态提示词注入器
├── speak_parser.py          # <speak tone=""> 标签流式解析 + 标签剥离
├── speak_queue.py           # 严格顺序队列调度 + 音频合并(AudioMerger)
├── audio_merger.py          # 多段 WAV 合并（交叉淡入淡出防爆音）
└── routes.py                # HTTP API 路由
```

### 核心设计决策

| 要点 | 方案 |
|------|------|
| **音色按角色区分** | TTS 配置存在 `character/<角色>/config.yaml` 的 `tts` 节点，默认音色 = 冰糖 |
| **音频缓存路径** | `.AIGEME/.data/local/<角色名>/tts-wav/<timestamp>_<turn_id>.wav` |
| **一轮对话合并存储** | 流式播放时分段下发，整轮结束后用 `AudioMerger.merge()` 合并为一段 WAV 保存（交叉淡入淡出 5ms 防爆音） |
| **重播逻辑** | 先查缓存，有就直接播放（不再重新合成） |
| **不朗读内容** | 提示词明确要求代码块、URL、表情标签等放 speak 标签外 |

### 前端模块

```
frontend/chat/js/tts.js      # 新增：TTS 播放控制模块
frontend/chat/index.html     # 修改：设置面板 + 播放按钮
frontend/chat/js/blocks.js   # 修改：audio block handler
frontend/chat/js/state.js    # 修改：TTS 状态字段
frontend/chat/js/app.js      # 修改：绑定开关 + 中断逻辑
```

### 核心数据流

```
LLM 流式输出
  → SpeakParser.feed(delta)          # 实时解析 <speak> 标签
  → [CompletedSpeak(text, tone, index)]
  → SpeakQueue.enqueue(item)         # 按 index 入队
  → MimoTTSClient.synthesize(text)   # 异步 TTS 合成
  → WebSocket audio block            # base64 WAV 下发
  → 前端 TTSPlayer.play()            # 按序播放
```

### Block 协议扩展

| Block 方向 | Block Type | 说明 |
|-----------|-----------|------|
| 服务端→客户端 | `audio` | 下发 TTS 合成音频（含 index） |
| 客户端→服务端 | `audio_play_end` | 通知播放完成 |
| 服务端→客户端 | `tts_state` | TTS 状态同步 |

### 动态提示词模板

TTS 开启时注入 system prompt：

```markdown
## 语音输出格式指导

当 TTS 语音功能开启时，请遵循以下格式：

1. **需要朗读的内容**使用 `<speak>` 标签包裹，tone 属性指定语气：
   ```
   <speak tone="语气描述">需要朗读的文本</speak>
   ```

2. **tone 属性**可选值：自然温和、兴奋、严肃、悲伤、急切、温柔、开心、疑惑、愤怒、平静
   也支持风格标签：东北话、磁性、唱歌
   可用自然语言描述：`tone="轻声细语，像是在说悄悄话"`

3. **不需要朗读的内容**（如动作描述、表情标签）放在 speak 标签外。

4. **示例**：
   ```
   <speak tone="兴奋">今天天气真好！</speak>
   她开心地笑了起来。
   <speak tone="温柔">我们去散步吧。</speak>
   <tachie-e>happy</tachie-e>
   ```

5. **多段标注**：不同语气拆成多个 speak 标签。
```

### 标签剥离规则（消息生命周期）

消息架构分为**内存列表**（用于回传 LLM）和**持久化**（用于落盘/恢复加载）。标签只在流式阶段存在：

```
LLM 流式输出（含 <speak> 标签）
  → SpeakParser 实时解析 TTS
  → 消息流式完成 → strip_tts_tags()
  → clean_text 写入内存列表
       ├── 回传 LLM 上下文：从内存列表读 clean_text
       ├── 前端渲染历史：从内存列表读 clean_text
       └── 持久化落盘：从内存列表直接写 clean_text
```

| 阶段 | 内容 | 标签状态 |
|------|------|---------|
| 流式处理中 | 原始 delta（供 SpeakParser 解析） | ✅ 含标签 |
| 消息完成 → 写入内存列表 | clean_text | ❌ 已剥离 |
| 回传 LLM 上下文 | 从内存列表读取 | ❌ clean_text |
| 持久化落盘 | 从内存列表直接写入磁盘 | ❌ clean_text |
| 恢复对话加载 | 从磁盘加载 → 内存列表 | ❌ 全程无标签 |
```

---

## ✅ 行动清单

| # | 行动 | Phase | 负责方 | 紧急度 | 期望完成 |
|---|------|-------|--------|--------|---------|
| 1 | 创建 `core/tts/` 模块目录 + __init__.py | P1 | 后端 | P0 | Day 1 |
| 2 | 实现 `config.py` - TTS 配置管理 | P1 | 后端 | P0 | Day 1 |
| 3 | 实现 `client.py` - MIMO API 封装 | P1 | 后端 | P0 | Day 1 |
| 4 | 实现 `routes.py` - HTTP API | P1 | 后端 | P0 | Day 1 |
| 5 | 实现 `speak_parser.py` - 标签解析器（含 tone 属性提取 + 标签剥离） | P2 | 后端 | P0 | Day 2 |
| 6 | 实现 `speak_queue.py` + `audio_merger.py` - 队列调度 + 音频合并 + 语言检测过滤 | P2 | 后端 | P0 | Day 2 |
| 7 | 实现 `prompt_injector.py` - 提示词注入（代码块/URL/非中英文放 speak 外） | P2 | 后端 | P0 | Day 2 |
| 8 | 修改 `loop.py` - Hook speak parser + 轮次结束触发音频合并缓存 | P2 | 后端 | P0 | Day 2 |
| 9 | 扩展 `blocks.py` - 新增 Block 类型 | P2 | 后端 | P0 | Day 2 |
| 10 | 创建 `tts.js` - 前端播放模块（含缓存重播逻辑） | P3 | 前端 | P0 | Day 3 |
| 11 | 修改 `index.html` - TTS 设置面板（按角色区分） | P3 | 前端 | P0 | Day 3 |
| 12 | 修改 `blocks.js` - audio block handler | P3 | 前端 | P0 | Day 3 |
| 13 | 修改 `state.js` + `app.js` - 绑定开关 | P3 | 前端 | P0 | Day 3-4 |
| 14 | 端到端集成测试（含缓存命中/合并验证） | P4 | 全员 | P1 | Day 4 |
| 15 | 中断/顺序/三种模式/多角色专项测试 | P4 | 全员 | P1 | Day 4 |

---

## ⚠️ 风险与注意事项

1. **API Key 安全**：TTS API Key 不应存储在 `settings.yaml`（版本化），应存入 `local.yaml` 或环境变量
2. **并发竞态**：多个 speak 标签的 TTS 合成时间不同，队列调度必须处理竞态条件
3. **错误容错**：TTS 合成失败不应阻塞文本输出；播放失败应静默 fallback
4. **WS 传输**：WAV base64 通过 WebSocket 传输，注意单次不宜过大（MIMO 上限 10MB）
5. **音频格式**：MIMO 返回 WAV 格式，前端 AudioContext 原生支持，无需转码

---

## 📚 成员产出索引

- **gstack-product-reviewer（产品评审员）** 原始产出：`docs/TTS_INTEGRATION_PLAN.md`
  - 8 个章节完整方案：后端架构、前端架构、API 设计、数据流图、提示词模板、数据结构、实施计划
- **gstack-designer（设计师）** 原始产出：`deliverables/gstack/tts-ui-prototype.html`
  - 4 大模块 UI 原型：设置面板、播放控制器、语音开关、队列状态栏
  - 交互模拟：播放/停止/中断/自动播放

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
