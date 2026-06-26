# AIGEME

**给 LLM 的脚手架，跟你一起长成最适合你的 Agent 框架。**

不是又一个大而全的 AI 框架。AIGEME 是一套极简的推理-行动引擎（~20000 行 Python），
给你最少的抽象、最干净的接口，然后放手让 LLM 自己长——
学会你的习惯，适配你的场景，变成你需要的样子。

UI 选了视觉小说前端——不是偶然。
不想做冷冰冰的终端工具，也不想做个只会聊天的傻 LLM。
立绘 + 表情 + 字幕式的对话流，让 Agent 有了面孔和情绪，
让交互有了节奏和温度。

引擎本身不挑前端——但前端选得用心。

---

## 设计原则

### 零号原则：只给规范，不做预设

市面上绝大多数 Agent 框架在做同一件事：**替 LLM 做决定**。
用什么向量库、怎么写模板、怎么编排链——框架替你想好了。

AIGEME 反过来：**给规范，但把决策权留给 LLM。**

| 模块 | 框架提供的（规范+基础能力） | 框架不做的（留给 LLM 自己长） |
|------|---------------------------|---------------------------|
| **记忆管理** | YAML frontmatter 解析、`[[wikilink]]` 图谱引擎、文件锁、查重合并、审计扫描 | 决定记什么、怎么分类、什么时候合并清理 |
| **技能系统** | SKILL.md 搜索/读取、`character/.skill/` 目录发现 | 决定什么时候用什么技能、怎么组合、要不要写新技能 |
| **工具系统** | document、memory、bash、browser、web search、skill | 自由组合工具完成任务，不分固定 workflow |
| **角色定义** | `soul.md` 注入、`expressions.yaml` 表情映射 | 塑造什么人格、怎么说话、什么风格 |

没有 chain。没有 graph。没有 LangChain 那套编排 DSL。  
没有嵌入式 RAG。记忆用文件锁 + wikilink，不需要 vector DB。  
没有 Plugin SDK。技能就是一页 Markdown，AI 读了自己干。  
没有预设的 Agent persona。角色是一个目录，你可以随时加。

框架实际做的事只有三件：
1. 一条干净的推理-行动循环（RaAct）
2. 一套够用的基础工具（读写文档、记记忆、搜网页、跑 Bash、控浏览器）
3. 一道不碍事的权限围栏

剩下的，交给 LLM——**每次对话都是训练，每次工具调用都是扩展。**  
框架跟用户一起长，最后长成最适合你的形状。

---

### 1. 角色 = 人格文件 + 工具权限 + 记忆沙盒

角色不是 prompt 里的一段描述。角色是一个目录：

```
character/<id>/
├── soul.md          # 人格定义（AI 读取以感知"我是谁"）
├── identity.md      # 开场白
├── expressions.yaml # 表情 → 立绘映射
├── config.yaml      # 注册信息 + 技能列表
└── .skill/          # 角色专用技能（SKILL.md 格式）
```

- `soul.md` 在每次推理时完整注入，不是摘要，不是 embedding。
- 每个角色拥有独立的记忆目录（`.AIGEME/.data/{user}/{char}/memory/`）。
- 角色切换不需要重启，只需要 WebSocket 重连——后端无会话状态。

### 2. RaAct（Reasoning-Action Cycle）

不是 Chat，是 **Plan → Execute → Observe → Think Again**。

```
LLM ──[推理（含思维链）]──→ tool_calls ──[并行执行]──→ 观察结果
   ↑                                                   │
   └────────────────── 下一轮推理 ←──────────────────────┘
```

关键细节：
- 每轮至多 8 次推理-行动迭代。
- 完整思维链跨轮保留（对 DeepSeek R1 等推理模型友好）。
- 上下文超限自动触发两级压缩：
  1. **结构清理** — 截断超长工具返回、删除低优先级 block、合并连续无决策对话
  2. **LLM 摘要** — 若结构清理后仍超限，调用 LLM 做深层摘要（`~reduce`）
- 若 LLM 返回 `ContextWindowExceededError`，强制暂停循环，执行压缩后重试。

### 3. 并行工具调度

不是串行。LLM 一次发多个 `tool_calls` 时，系统按 **读/写/复合** 三类分组调度：

| 分类 | 调度策略 | 示例 |
|------|---------|------|
| Read | `asyncio.gather` 全并行 | document.read, memory.search |
| Write | 文件级锁分组，同路径串行，不同路径并行 | document.write, memory.add |
| Compound | 顺序串行 | bash, python, plan.execute |

- 写操作使用 `_WriteGroupScheduler`：解析 `resolve_resource_path` 获取文件路径，路径相同的写操作串行，不同的并行。
- 返回顺序与 LLM 请求的 `tool_calls` 严格一致（OpenAI API 协议的格式要求）。
- 工具调用和结果通过 WebSocket 实时推送（`tool_call` / `tool_result` block），UI 即时展示。

### 4. 工具注册中心 + 权限链

`ToolRegistry` 是单例。注册流程：

```
register(tool) → PermissionChain.add(filter) → 执行链路：

RateLimiter ──→ PermissionChain ──→ JSON Schema 校验 ──→ execute()
                  │
                  ├─ RequireConfirmFilter（声明式规则匹配）
                  ├─ BlocklistFilter（声明式拒绝规则）
                  └─ ZonePermissionFilter（按路径区域判权）
```

- **RateLimiter**: 滑动窗口，每 session 30 次/60s。
- **PermissionChain**: 责任链模式，任意 filter 拒绝或标记需确认即短路。
- **JSON Schema 校验**: 检查必需字段、类型、枚举值，阻止格式错误的工具调用执行。
- **ZonePermissionFilter**: 按路径区域自动判权
  - `writable`（`.AIGEME/`, `character/`, `tachi-e/`） → 自动读写
  - `readonly`（`core/`, `config/`） → 只读
  - `system`（`C:\Windows` 等） → 拒绝
  - `external` → 需确认
- `needs_confirm` 支持二阶段确认：用户确认后，registry 传递 `_confirmed=True` 重试，若工具内部仍需强制确认则传递 `_force=True`。

### 5. System Prompt KV 缓存优化（Prefix Caching）

不是所有 AI 框架都在意这个。我们在意。

**问题**：vLLM / OpenAI 等推理服务支持 prefix caching（KV 缓存复用），
但前提是每次请求的 system prompt 前缀必须完全一致。
如果每轮往 system prompt 里塞不同的时间或提醒，KV 缓存永远命中不了。

**方案**：两段式 Prompt 组装（`PromptAssembler`）：

```
System Message（固定）           User Message（每轮不同）
├─ 行为准则                      ├─ 当前时间
├─ 角色设定（soul.md）            ├─ 工具优先指令
├─ 用户画像（USER.md 静态内容）    ├─ 记忆行为提醒
├─ 记忆概览（index 摘要）          ├─ 记忆整理提醒
├─ 可用表情列表                    ├─ 待办到期提醒
├─ 已加载技能列表                  └─ ...（所有每轮变化的内容）
├─ 系统环境信息
├─ 工具 JSON Schema
└─ 工作区路径
```

- system prompt 只在角色加载时构建一次，此后在 session 生命周期内完全不变。
- 所有动态内容通过 `build_variable_content()` 单独组装，由循环引擎注入为 user 消息。
- 测试（`context_test.py`）明确验证 `{{current_time}}` **不参与** system prompt 渲染。

**流式取消也小心**：当用户中断流式请求时，`instructor_client.py` 用 `stream.aclose()`
优雅关闭而非强制断连，让 vLLM 端保持 KV 缓存供下一轮复用（注释标注了原因代码）。

当你在 vLLM 部署上跑这个引擎时，KV cache 命中率能省下 30-50% 的首 token 推理时间。

---

### 6. 记忆系统（File-Wiki）

不是 Embedding。不是向量库。文件级 YAML frontmatter + 双向链接图谱 + 倒排索引。

```
.AIGEME/.data/{user}/{char}/memory/
├── MEMORY.md    # 索引文件（文件级元数据表）
├── LINKS.md     # 双向链接图谱（节点表 + 链接表 + 断链表）
├── _archive/    # 软删除归档
├── 事实.md      # 单文件存储，YAML frontmatter 包裹
├── 情感.md
└── ...
```

**YAML frontmatter** 每个记忆文件自带：
```yaml
---
id: uuid-v4
type: event | fact | process | emotion | reflection | decision | summary | preference
created: 2026-06-23 17:00:00
updated: 2026-06-23 17:30:00
checksum: sha256-of-body
tags: [galgame, AI, 引擎]
links: [关联文件, 其他文件]
source: user | agent | reflection
round: 0
status: active | archived | deprecated
---
```

**双向链接图谱**（`LinkGraph` 引擎）：
- 自动解析 `[[wikilink]]` 语法建立双向关联。
- 图谱存储在 `LINKS.md` 中：节点表（文件名/标签/引用次数/摘要）+ 链接表（来源/目标/关系/建立时间）+ 断链表。
- 扫描 `add`/`edit` 操作自动触发 `update_links`：diff 新旧链接集，增量更新。
- `detect_dead_links` 检查链接目标文件是否存活。
- `find_orphans` 发现不在图谱中的孤立文件。

**图谱扩散检索**（`graph_search`）：
- 从种子节点开始 BFS 扩散，逐层收集关联记忆。
- `max_depth` 控制扩散层数，`min_relevance` 基于引用次数过滤。
- 结果合并去重后返回。

**查重合并**（`check_similar` + `merge`）：
- 正文级别相似度比较（SequenceMatcher）。
- 相似度 ≥ 0.7 → 自动合并到已有文件。
- 合并后原文件归档到 `_archive/`。

**权限保护**：
- 所有记忆文件操作用 `LockManager`（`file_lock.py`）做文件级读写锁保护。
- 核心文件（`MEMORY.md`、`LINKS.md`）不可被删除。

**但框架不替 LLM 决定怎么用记忆**——不预设什么该记住、什么时候该检索、什么时候该合并。
这些是 LLM 在 `tool memory` 描述里读了 SOP 自己执行的。**规范在手，决策在 AI。**

### 7. 技能系统（SKILL.md）

没写 Plugin SDK。每一张 SKILL.md 就是一页说明书——AI 读了然后自己调用工具干活。

```
.AIGEME/.skill/<name>/SKILL.md      # 全局技能，所有角色可用
character/<id>/.skill/<name>/SKILL.md  # 角色专用技能
```

SKILL.md 格式：
```yaml
---
name: create_character
description: 创建新角色。创建 soul.md、identity.md、expressions.yaml、立绘目录和 config.yaml
version: 1.0.0
author: AIGEME
trigger: 用户要求"创建角色"、"新增角色"、"添加角色"
parameters:
  - name: char_id
    type: string
    description: 角色唯一 ID
    required: true
---
## 功能
...markdown 指令体，引用 document/browser/bash 等工具完成工作...
```

系统本身只有 `search`（根据 name/description 关键词匹配）和 `use`（读取完整 SKILL.md）两个操作。**不做调用框架，只做知识分发。**

框架不做技能编排，不写 workflow。什么时候用什么技能、怎么组合多个技能——
**那是 LLM 读过说明书后自己决定的事。**

---

## 工程实现亮点

### Fixed/Variable 上下文隔离

`PromptAssembler.build_system_prompt()` 和 `build_variable_content()` 的分离
是整个引擎的**第一个架构决策**：

- **固定部分**包含角色设定、工具 Schema、记忆索引、表情列表、行为准则——
  这些内容在 session 中从头到尾不变。
- **动态部分**包含时间戳、工具提醒、待办到期通知——每轮必然变化。

```python
# context.py — 调用方
system = assembler.build_system_prompt()        # 不变的 → role: system
user_var = assembler.build_variable_content()    # 变化的 → role: user
messages = [SystemMessage(content=system), ...HumanMessage(content=user_var)]
```

效果：vLLM 前缀缓存命中率最大化，首 token 延迟降低，API 调用费用也因减少重复 token 而略降。

### 异步文件锁（`file_lock.py`）

```python
async with lm.acquire("/path/file.md"):       # 写锁（互斥）
async with lm.acquire_read("/path/file.md"):  # 读锁（共享）
```

- 基于 `asyncio.Lock` + 路径级 key 的 per-file 锁映射。
- 读读不互斥，读写互斥。不使用文件系统锁（避免跨平台问题）。

### 输出类型系统（`base.py`）

每个工具声明自己的 `output_type`，registry 执行后公开该类型，供块协议消费者（`loop.py`）按类型解析：

```python
ToolOutputType = Literal["text", "json", "bash", "file_read", "file_list",
                         "file_search", "skill_search", "skill_content", "image"]
```

- `image` 类型自动提取 `data_url` 注入为多模态 user 消息，供 LLM 分析。
- 这是块协议与工具系统的唯一耦合点——新增工具只需声明类型，无需接触循环引擎。

### 审计日志

所有工具调用通过 `logging.info` 记录结构化日志：

```
[AUDIT] session=xxx tool=document args={...} result=ok
[AUDIT] session=xxx tool=memory args={...} result=blocked(permission:...)
```

覆盖率：register → permission → schema validation → execution → result。

### WebSocket 块协议（10+ block types）

每一条 WebSocket 消息是一个 `Block`：

```python
@dataclass
class Block:
    block_type: str  # thinking | speech | tool_call | tool_result | error
                     # | confirm | plan_step | system | status | stt | tts
    delta: str
    metadata: dict
    final: bool
    created_at: str
```

- 流式：`thinking` block 逐 token 推送（Delta 技术）。
- 确认对话框：`confirm` block → 用户确认后回传 `user_confirm` 消息。
- UI 按 `block_type` 渲染不同的 HTML 块（10+ 渲染器）。

---

## 依赖哲学

核心依赖仅两个：
- **litellm** — LLM 调用抽象（27+ provider，同一接口）
- **instructor** — 结构化输出（工具调用强制 JSON Schema）

不做大而全的框架依赖。不用 LangChain，不用 Semantic Kernel。20000+ 行的项目核心代码，第三方依赖一只手数得过来。

---

## 量级

```
  core/          ~20000 行  Python  后端核心
  frontend/      ~3000  行  Vanilla JS    前端
  character/     ~400   行  MD + YAML     角色定义
  .skill/        3      个  技能文档
```

没有 TypeScript 编译步骤，没有构建管线，`start.bat` 双击即跑。

---

## 代码阅读建议

如果你在读代码：

1. **`core/main.py`** — FastAPI 应用工厂，入口点。看工具怎么注册、过滤器怎么组装、WebSocket 路由怎么挂载。
2. **`core/engine/context.py`** — Prompt 组装器。看 Fixed/Variable 分离和 KV 缓存优化的具体实现。
3. **`core/raact_loop/loop.py`** — 核心循环。看上下文如何管理、压缩如何触发、并行如何调度。
4. **`core/tools/parallel.py`** — 并行调度器。看读/写/复合分组 + 文件锁怎么写。
5. **`core/memory/link_graph.py`** — 图谱引擎。看双向链接如何用文件实现图的读写。
6. **`core/tools/permission.py`** — 权限框架。看责任链 + 区域权限。
7. **`core/tools/registry.py`** — 工具执行链路。看速率限制 + 权限 + 校验 + 审计的完整流程。

---

> **AIGEME** — 给 LLM 一把好用的梯子，剩下的它自己会长。
>
> MIT License · personal project · built with ❤️ and 🎀
