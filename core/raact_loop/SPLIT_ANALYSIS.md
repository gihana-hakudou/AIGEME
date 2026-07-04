# 拆分分析报告：loop.py + main.py

---

## 一、loop.py 分析 (1028 行)

### 1.1 顶层函数

| 函数 | 行号 | 行数 | 说明 |
|------|------|------|------|
| `_strip_images_from_messages` | 31-55 | 25 | 从 messages 移除图片（多模态降级用） |
| `_is_multimodal_error` | 58-66 | 9 | 判断是否因不支持多模态导致的错误 |
| `_is_tool_choice_incompatible_error` | 69-79 | 11 | 判断 tool_choice 是否与当前模型不兼容 |
| `_extract_tool_content` | 82-283 | **201** | 从工具返回值提取文本给 LLM（含 9 种 output_type 分支） |

### 1.2 RaActLoop 类 (285-1027, 743 行) — 共 11 个方法

| 方法 | 行号 | 行数 | 职责 | 可抽出？ |
|------|------|------|------|----------|
| `__init__` | 288-318 | 31 | 初始化所有依赖 | 否（构造器） |
| `set_cancelled_ref` | 320-322 | 3 | 设置取消状态引用 | 否（1行设置器） |
| `set_confirm_refs` | 324-331 | 8 | 设置确认对话框引用 | 否（1行设置器） |
| `_cancelled` (property) | 333-336 | 4 | 获取取消状态 | 否（简单属性） |
| `_handle_cancelled_round` | 338-376 | 39 | 用户取消时的清理逻辑 | **可抽出** |
| `_pending_confirm` (property) | 378-382 | 5 | 获取确认 Event | 否（简单属性） |
| `_confirm_result` (property) | 384-388 | 5 | 获取确认结果 | 否（简单属性） |
| `raact_stream` | 390-936 | **547** | 主循环（入口 + 所有逻辑） | **可局部提取** |
| `_build_assistant_message` | 938-957 | 20 | 构建 assistant 消息 dict | **可抽出** |
| `_msg_to_dict` | 959-1001 | 43 | BaseMessage → dict 转换 | **可抽出** |
| `_dict_to_message` | 1003-1027 | 25 | dict → BaseMessage 转换 | **可抽出** |

### 1.3 `_extract_tool_content` 分析 (201 行)

**功能**: 将工具调用的返回结果转换为 LLM 可读的文本。按 `output_type` 有 9 种解析分支：

1. `bash` (stdout/stderr/returncode) 
2. `file_read` (文件摘要)
3. `file_list` (目录列表，最多30项)
4. `file_search` (文本搜索结果)
5. `skill_search` (技能列表)
6. `skill_content` (技能详情)
7. `image` (图片元数据)
8. 兜底启发式: 检测 `stdout` 字段
9. 兜底启发式: 检测 `message` + `count=0`

**评估**: ✅ 天生适合独立模块！原因：
- 没有 `self` 依赖（纯函数）
- 逻辑独立，不依赖外部状态
- 每种 output_type 的分支可以进一步拆为独立处理函数
- 返回值类型稳定（str）

### 1.4 RaActLoop 核心拆分逻辑

`raact_stream` (547 行) 内部可按阶段提取：

| 代码段 | 行号 | 说明 | 适合提取为？ |
|--------|------|------|------------|
| 消息构建阶段 | 411-475 | `build_messages()` | 独立方法 |
| 上下文压缩 | 428-444 | 压缩检查 + LLM 深度压缩 | 已在 ContextCompressor |
| 记忆注入 | 452-468 | 按需检索记忆 | 独立方法 |
| LLM 调用 + 异常处理 | 537-643 | 含多模态降级、tool_choice 降级 | 核心公共逻辑 |
| 工具并行执行 | 673-719 | 推送 tool_call + 并行执行 | 可在 parallel.py |
| 确认处理 | 726-797 | needs_confirm 逐个确认 | **可抽出为独立模块** |
| 工具结果拼接 | 805-893 | tool_result + 最终回复 | 处理循环逻辑难抽 |

### 1.5 文件导入关系

**外部引用 loop.py 的文件：**
- `core/ws_server.py:20` → `from core.raact_loop.loop import RaActLoop`
- `core/plan/executor.py:8` → `from core.raact_loop.loop import RaActLoop`

**loop.py 自身导入：**
- `core.raact_loop.stream_router` → `route_response` (函数)
- `core.engine.compressor` → `ContextCompressor` (类)
- `core.engine.context` → `PromptAssembler` (类)
- `core.engine.instructor_client` → `InstructorClient` (类)
- `core.engine.models` → `RaActResponse`
- `core.protocols.blocks` → `Block`
- `core.tools.registry` → `ToolRegistry`
- `core.tools.parallel` → `ParallelExecutor, ToolCallDef`
- `core.memory.memory_tracker` → `MemoryContextTracker`
- `core.config.settings` → `get_config`

### 1.6 loop.py 拆分建议

**方案：拆成 3 个文件**

```
core/raact_loop/
├── __init__.py                    # 空或 re-export
├── loop.py                        # RaActLoop 核心 + raact_stream
├── message_utils.py     [新增]    # _msg_to_dict, _dict_to_message, _build_assistant_message
├── tool_content.py      [新增]    # _extract_tool_content + output_type 子处理器
└── error_utils.py       [新增]    # _is_multimodal_error, _is_tool_choice_incompatible_error
```

**具体分配：**

| 新文件 | 内容 | 原因 |
|--------|------|------|
| `tool_content.py` | `_extract_tool_content()` + 每种 output_type 的处理函数 | 201 行纯函数，独立逻辑，无 `self` |
| `message_utils.py` | `_msg_to_dict()`, `_dict_to_message()`, `_build_assistant_message()` | 88 行消息序列化/反序列化，独立工具函数 |
| `error_utils.py` | `_is_multimodal_error()`, `_is_tool_choice_incompatible_error()`, `_strip_images_from_messages()` | 45 行错误检测/降级工具函数 |
| `loop.py` (保留) | `RaActLoop` 类 + `raact_stream` 主循环（清理后约 650 行） | 核心业务逻辑，构成本身耦合 |

---

## 二、main.py 分析 (792 行)

### 2.1 文件结构概览

| 段 | 行号 | 行数 | 内容 |
|----|------|------|------|
| 日志配置 | 10-22 | 13 | core logger 初始化 |
| 导入 | 24-46 | 23 | FastAPI + 工具类 |
| 工具函数(×5) | 51-107 | 57 | `_split_model`, `_set_user_env_var`, `_del_user_env_var`, `_get_perm_mode`, `_set_perm_mode` |
| 常量/配置 | 86-135 | 50 | `PROJECT_ROOT`, `PROVIDER_DEFAULTS` |
| 诊断日志 | 110-116 | 7 | `diag()` |
| MCP 接线 | 138-214 | 77 | `_connect_mcp_servers`, `_disconnect_mcp_servers`, `_refresh_mcp_tools` |
| `create_app()` | 217-791 | **575** | 主函数（占全文件的 72%） |

### 2.2 create_app() 内部的所有路由端点

| 路由 | 方法 | 行号 | 约行数 | 分组 |
|------|------|------|--------|------|
| `/ws/{character_id}` | WebSocket | 318-326 | 9 | WebSocket |
| `/api/characters` | GET | 328-342 | 15 | 角色 |
| `/api/health` | GET | 344-346 | 3 | 健康检查 |
| `/api/confirm` | POST | 349-361 | 13 | 确认 |
| `/api/conversations/{character_id}` | GET | 363-395 | 33 | 对话历史 |
| `/api/conversations/{character_id}/{date}` | GET | 397-415 | 19 | 对话历史 |
| `/api/characters/{character_id}/skills` | GET | 421-427 | 7 | 角色技能 |
| `/api/workspace` | GET | 429-454 | 26 | 工作区文件 |
| `/api/characters/{character_id}/memory` | GET | 456-466 | 11 | 记忆索引 |
| `/api/settings` | GET | 468-493 | 26 | 读取配置 |
| `/api/llm-providers` | GET | 495-567 | **73** | LLM Provider列表 |
| `/api/llm-providers/{provider_id}/models` | GET | 569-655 | **87** | 获取模型列表 |
| `/api/settings` | PUT | 657-745 | **89** | 更新配置 |
| `/` | GET | 778-789 | 12 | 首页 |

**可清晰分组的端点：**
1. **角色相关**: `/api/characters`, `/api/characters/{id}/skills`, `/api/characters/{id}/memory`
2. **对话相关**: `/api/conversations/{id}`, `/api/conversations/{id}/{date}`
3. **配置/设置**: `/api/settings` (GET+PUT), `/api/llm-providers`, `/api/llm-providers/{id}/models`
4. **系统**: `/api/health`, `/api/confirm`, `/api/workspace`
5. **前端**: `/` (index.html), `/ws/{character_id}`
6. **静态文件**: `/static`, `/tachi-e` (app.mount)

### 2.3 create_app() 内的非路由代码

除了路由，`create_app()` 还包含大量「粘合代码」：

| 代码 | 行号 | 行数 | 说明 |
|------|------|------|------|
| lifespan 定义 | 222-233 | 12 | startup/shutdown |
| FastAPI 对象 | 235-240 | 6 | app 创建 |
| CORS | 242-249 | 8 | 中间件 |
| 静态文件 | 251-259 | 9 | /static + /tachi-e |
| 工具注册 | 261-299 | 39 | registry, browser, MCP 回调 |
| 配置读取 | 302-305 | 4 | LLM 模式 |
| WS Server 初始化 | 310-315 | 6 | WSServer 对象 |
| 辅助函数(内联) | 748-776 | 29 | `_file_hash`, `_inject_version` |

### 2.4 文件导入关系

**外部引用 main.py 的文件：**

| 文件 | 导入符号 | 用途 |
|------|----------|------|
| `G:/AIGEME/main.py:8` | `create_app` | ASGI 入口，`uvicorn` 启动 |
| `core/main.py:681` | `_split_model` | **自引用**（update_settings 内） |
| `core/ws_server.py:145` | `PROVIDER_DEFAULTS` | 惰性导入 |

**影响面很小**：只有 3 处导入，其中一处还是自引用（可改为内部函数导入消除循环）。

### 2.5 main.py 拆分建议

**方案：拆成 4 个文件**

```
core/
├── main.py                          # 精简：仅 create_app() + lifetime orchestration
├── routes/
│   ├── __init__.py                  # 汇总所有 router
│   ├── characters.py     [新增]     # /api/characters, /api/characters/{id}/skills, /api/characters/{id}/memory
│   ├── conversations.py  [新增]     # /api/conversations/{id}, /api/conversations/{id}/{date}
│   ├── settings.py       [新增]     # /api/settings (GET+PUT), /api/llm-providers, /api/llm-providers/{id}/models
│   ├── system.py         [新增]     # /api/health, /api/confirm, /api/workspace
│   └── frontend.py       [新增]     # / (index.html with version injection), /ws/{character_id}
├── mcp_lifespan.py       [新增]     # _connect_mcp_servers, _disconnect_mcp_servers, _refresh_mcp_tools
└── utils.py              [新增]     # _split_model, _set_user_env_var, _del_user_env_var,
                                     #   _get_perm_mode, _set_perm_mode, diag, _file_hash, _inject_version
```

**迁移步骤（不修改任何文件的最小风险方案）：**

1. 新建 `core/routes/__init__.py` — 导入所有 route modules 的 router
2. 新建 `core/routes/characters.py` — `APIRouter(prefix="/api")` 
3. 新建 `core/routes/conversations.py` — `APIRouter(prefix="/api")`
4. 新建 `core/routes/settings.py` — `APIRouter(prefix="/api")`
5. 新建 `core/routes/system.py` — `APIRouter(prefix="/api")`
6. 新建 `core/routes/frontend.py` — 首页 + WebSocket 路由
7. 新建 `core/mcp_lifespan.py` — MCP 接线函数
8. 新建 `core/utils.py` — 工具函数
9. **最后**精简 `core/main.py`：remove 所有已提取代码，改为 `app.include_router(...)` 引入各 router

**关键设计决策：**
- **`registry` 和 `ws_server` 对象**：需要从 `create_app()` 传递到各 router。建议使用 FastAPI `app.state` 或显式依赖注入（每个 route module 输出一个带参数的 `init_router` 函数）。
- **`update_settings`** 自引用了 `_split_model` — 提取后该行改为 `from core.utils import _split_model`。
- **`PROVIDER_DEFAULTS`** 被 `ws_server.py` 惰性导入 — 提取后改为 `from core.utils import PROVIDER_DEFAULTS`。

---

## 三、总结对比

| 维度 | loop.py | main.py |
|------|---------|---------|
| 总行数 | 1028 | 792 |
| 拆分建议 | 3 个文件 | 4 个文件 |
| 主要问题 | `_extract_tool_content` 201 行 | `create_app()` 575 行 (72%) |
| 外部分散引用 | 2 处 (ws_server, executor) | 3 处 (main.py, ws_server, 自引用) |
| 核心挑战 | 保持 RaActLoop.__init__ 接口不变 | registry + ws_server 对象传递方式 |
| 拆分收益 | 高（纯函数分离，降低理解成本） | 高（路由按功能分组，粘合代码分离） |
