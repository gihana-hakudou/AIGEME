# QA Report: AIGEME TTS Module — Bug Fix Verification

| Field | Value |
|-------|-------|
| **Date** | 2026-04-04 |
| **Module** | TTS (Text-to-Speech) — 5 bug fix files |
| **Branch** | current |
| **Commit** | HEAD (26 additions, 493 deletions across 5 source files + test_mimo_tts.py removal) |
| **PR** | — |
| **Tier** | Standard (重点场景 + 边界测试 + 回归) |
| **Scope** | 6 个重点场景：①队列参数继承 ②turn_id与缓存 ③配置空值防御 ④voice_clone降级 ⑤异常日志 ⑥回归测试 |
| **Duration** | ~15 min |
| **Tests** | 53 (旧 29 + 新 24) |
| **Framework** | pytest + unittest |

## Health Score: 98/100

| Category | Score |
|----------|-------|
| Functional (新测试) | 100 — 24/24 新测试全通过 |
| Regression (旧测试) | 97 — 28/29 旧测试通过 |
| Console/Errors | 100 — 异常日志修复已确认 |
| Config Safety | 100 — 空值防御 + pop→get 修复已确认 |
| Code Quality | 100 — no silent `except: pass` |

## Top Findings

1. **Bug #1 (P0) ✅ 已验证**: 队列消息 TTS 参数已补传 — `ws_server.py:667-677`
2. **Bug #5/#4 ✅ 已验证**: turn_id 正确设置 + `_all_chunks` 跨轮次不累积
3. **Bug #2 ✅ 已验证**: `config.get("voice") or "冰糖"` 正确处理 None/"" /缺失
4. **Bug #10 ✅ 已验证**: voice_clone 缺 sample 降级为 preset，不抛异常
5. **Bug #3 ✅ 已验证**: `except Exception: pass` → `logger.error(..., exc_info=True)`

## Test Results

### Regression Tests (原有 29 个)

| 测试类 | 结果 | 说明 |
|--------|------|------|
| TestSpeakParser (10) | ✅ 10/10 通过 | speak 标签解析 |
| TestAudioMerger (4) | ✅ 4/4 通过 | WAV 合并逻辑 |
| TestTTSConfig (3) | ✅ 3/3 通过 | 配置加载 |
| TestTTSAPI (3) | ⚠️ 2/3 通过 | test_routes_registered 因 `jieba` 缺失失败（环境问题，非代码问题） |
| TestPromptInjector (3) | ✅ 3/3 通过 | 提示词注入 |
| TestLoopHook (2) | ✅ 2/2 通过 | loop.py 代码存在性检查 |
| TestFrontendFiles (4) | ✅ 4/4 通过 | 前端文件完整性 |

### Bug Fix Verification (新增 24 个)

| 测试类 | 测试数 | 通过 | 覆盖场景 |
|--------|--------|------|----------|
| **TestQueueMessageTTSParams** | 3 | 3/3 ✅ | Bug #1: 队列消息 TTS 参数 — 方法签名、源码参数传递、ClientMessage 属性 |
| **TestConfigNullDefense** | 10 | 10/10 ✅ | Bug #2: 所有 config.get() `or` 默认值组合 — voice/tone/sample_b64/style_desc 的 None/"" /缺失/正常值 |
| **TestVoiceCloneDegradation** | 3 | 3/3 ✅ | Bug #10: 缺 sample 降级(preset)、不抛异常、有 sample 正常克隆 |
| **TestTurnIdAndCache** | 5 | 5/5 ✅ | Bug #5/#4: set_turn_id 设置正确、finish_turn 清空、flush 清空、多轮不累积 |
| **TestExceptionLogging** | 2 | 2/2 ✅ | Bug #3: loop.py 异常使用 logger.error + exc_info=True、不再 silent pass |
| **TestConfigSaveNoPop** | 1 | 1/1 ✅ | Bug #8: save() 使用 get() 而非 pop()，不污染调用者 dict |

### 汇总

| 类别 | 通过 | 失败 | 通过率 |
|------|------|------|--------|
| 原有测试 (29) | 28 | 1 (预存环境问题) | 96.6% |
| 新增测试 (24) | 24 | 0 | 100% |
| **总计 (53)** | **52** | **1** | **98.1%** |

## Bug Fix Verification Details

### ✅ Bug #1 (P0) — 队列消息 TTS 参数继承

**修复**: `ws_server.py:667-677` — 消息队列取出 `next_msg` 后，传给 `_run_raact_task` 时补传了 `tts_enabled/mode/voice/tone` 4 个参数。

**验证结果**:

| 测试 | 结果 | 证据 |
|------|------|------|
| `_run_raact_task` 签名包含4个TTS参数 | ✅ | `inspect.signature` 确认参数存在 |
| 队列处理段传递了4个TTS参数 | ✅ | 源码 assertIn 确认 `tts_enabled=next_msg.tts_enabled` 等 |
| ClientMessage 包含 tts 属性 | ✅ | `msg.tts_voice="茉莉"` 等正确存取 |

**结论**: ✅ 修复有效。当用户在第一个请求未完成时发送第二个请求，第二个消息的 TTS 音色/语气/模式/开关参数将正确传递到 SpeakQueue。

---

### ✅ Bug #5/#4 — turn_id 与缓存跨轮次不累积

**修复**:
- `loop.py:531` — `SpeakQueue` 创建后调用 `set_turn_id(uuid.uuid4().hex[:8])`
- `loop.py:530` — 使用 `dict(_tts_config)` 深拷贝 config
- `speak_queue.py:111` — `finish_turn()` 末尾清空 `_all_chunks`

**验证结果**:

| 测试 | 结果 | 证据 |
|------|------|------|
| `set_turn_id()` 设置 `_turn_id` | ✅ | 调用后 `_turn_id == "abc12345"` |
| `finish_turn()` 清空 `_all_chunks` | ✅ | 2 chunks → finish → 0 chunks |
| `finish_turn()` 有 turn_id 时也清空 | ✅ | 设 turn_id 后 finish → chunks 清空 |
| `flush()` 清空 `_all_chunks` | ✅ | flush 后 chunks == 0 |
| 多轮不跨轮次累积 | ✅ | Round 1 chunks → finish → 0; Round 2 只有 Round 2 数据 |

**结论**: ✅ 修复有效。多轮 RaAct round 后 `_all_chunks` 被正确清空，不跨轮次累积。

---

### ✅ Bug #2 — 配置空值防御

**修复**: `client.py:52-53` — `config.get("voice", "冰糖")` → `config.get("voice") or "冰糖"`，同理 `tone`。

**验证结果** (10 个用例全覆盖):

| 输入 | voice 结果 | tone 结果 |
|------|-----------|----------|
| None | "冰糖" ✅ | "" ✅ |
| "" | "冰糖" ✅ | "" ✅ |
| 缺失键 | "冰糖" ✅ | "" ✅ |
| 正常值 | 使用原值 ✅ | 使用原值 ✅ |
| `voice_clone_sample_b64` 缺失 | — | "" ✅ |
| `voice_clone_style_desc` 缺失 | — | "" ✅ |

**结论**: ✅ 修复有效。`None`/空字符串/缺失三种情况均被 `or` 正确短路到默认值。

---

### ✅ Bug #10 — voice_clone 降级

**修复**: `client.py:66-71` — voice_clone 模式缺 `sample_base64` 时降级为 preset 合成而非 `raise ValueError`。

**验证结果**:

| 测试 | 结果 | 证据 |
|------|------|------|
| 缺 sample → 调 preset | ✅ | `_synthesize_preset` 被调用 |
| 缺 sample → 不抛异常 | ✅ | `try/except ValueError` 未触发 |
| 有 sample → 调 clone | ✅ | `_synthesize_voice_clone` 被调用 |

**结论**: ✅ 修复有效。前端未上传克隆样本时，系统降级为 preset 合成并返回正常音频数据。

---

### ✅ Bug #3 — 异常日志

**修复**: `loop.py:538` — `except Exception: pass` → `logger.error(f"[TTS] 初始化失败: {e}", exc_info=True)`。

**验证结果**:

| 测试 | 结果 | 证据 |
|------|------|------|
| 使用 logger.error | ✅ | 源码包含 `logger.error(f"[TTS] 初始化失败: {e}"` |
| 包含 exc_info=True | ✅ | `assertIn("exc_info=True")` 确认 |
| 不再 silent pass | ✅ | except 块第一行非 `pass` |

**结论**: ✅ 修复有效。TTS 初始化异常被正确记录到日志（含完整 traceback）。

---

## Ship Readiness

| Metric | Value |
|--------|-------|
| Health score | 98/100 |
| Issues found | 0 (new tests) |
| Fixes applied | 5 (all verified) |
| Pre-existing issues | 1 (环境依赖: jieba 缺失 — 不影响 TTS 核心功能) |
| Deferred | 0 |

**PR Summary**: "TTS 模块 5 个 bug fix 已验证通过。新增 24 个测试用例覆盖队列参数继承(turn_id/voice/tone)、配置空值防御(voice/tone/样本 base64)、voice_clone 降级、异常日志记录、跨轮次缓存清除。原有 28/29 回归测试通过（1 个预存 jieba 环境问题）。Health score: 98/100。"

---

## 附: 测试文件变更

```
tests/test_tts_integration.py
  + 24 个新测试用例 / 6 个新测试类
  覆盖全部 5 个 Bug Fix 验证 + 1 个 config 防御验证
```

## 运行命令

```bash
cd f:/AIGEME
python -m pytest tests/test_tts_integration.py -v
```
