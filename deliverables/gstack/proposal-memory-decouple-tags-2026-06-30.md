# 记忆工具解耦 & 搜索召回优化 — 方案设计

**日期**：2026-06-30
**场景**：产品评审 + 架构设计
**参与成员**：产品官（gstack-product-reviewer）+ 排障手（gstack-investigator）

---

## 📌 TL;DR

- **整体结论**：🟢 方案可行，分 3 个 Phase 独立交付，无需数据迁移
- **计划总工时**：3~5 天
- **核心变更**：MemoryTool 拆出 ReminderTool + tags 字段暴露 + 搜索排序改进

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟢 Phase 1 可以立即开始 |
| 阻塞项 | context.py 文案需同步修改（否则 LLM 仍调老的 task 操作） |
| 关键行动项 | 3 个 Phase，共 8 个子任务 |
| 建议负责人 | 工程开发 |

---

## 1. 各成员核心结论

### 📋 产品官（产品评审）
- **解耦必要性高**：记忆和提醒是正交功能，LLM 独立调度更自然
- **建议先做 Phase 1（解耦），Phase 2+3（tags+搜索）无依赖，可并行**
- 重点注意：context.py 中到期提醒注入文案必须同步修改，否则 LLM 会调已经不存在的 task 操作

### 🔧 排障手（调查分析）
- **TaskManager 已经是独立类**，数据存储（reminders/ 子目录）和格式完全隔离
- **tags 字段已存在于 frontmatter 但始终为空**（`tags: []`），倒排索引不爬 frontmatter
- 已有 `_tag_search()` 方法但仅在 graph_search 降级时用，完全没发挥价值
- embedding 向量检索建议暂缓（高成本，当前 tags 已能覆盖主要场景）

---

## 2. 综合方案设计

### Phase 1：工具解耦（1~2 天）

**现状**：MemoryTool 内部 `operation=="task"` 分支委派给 TaskManager
**目标**：拆出独立 ReminderTool，LLM 可直接调用 `reminder(operation="add", ...)`

#### 新工具 ReminderTool schema

| 字段 | 类型 | 说明 |
|------|------|------|
| `operation` | enum | add / done / cancel / list / read |
| `content` | string | 提醒内容，add 时必填 |
| `trigger_at` | string | 触发时间，add 时必填 |
| `repeat` | string? | daily/weekly/monthly |
| `id` | string | 任务ID，done/cancel/read 时必填 |
| `status` | string? | 筛选状态，list 时可选 |

#### 修改清单

| 文件 | 改动 |
|------|------|
| `core/memory/reminder_tool.py` | **新建**：ReminderTool 类（~80行） |
| `core/memory/tools.py` | MemoryTool 移除 task 分支 + 相关参数 |
| `core/main.py` | 导入并注册 ReminderTool |
| `core/ws_server.py` | 增加 `reminder_tool.set_char_id()` |
| `core/engine/context.py` | 到期提醒注入文案从 `memory(operation=task...)` 改为 `reminder(operation=done...)` |
| MemoryTool description | 移除 task 说明 |
| ReminderTool description | 编写独立的待办管理规范 |

### Phase 2：tags 字段暴露（1 天）

**现状**：frontmatter 有 `tags: []` 但永远为空
**目标**：MemoryTool.add 增加 `tags` 参数，写入 frontmatter

| 字段 | 类型 | 说明 |
|------|------|------|
| `tags` | `array[string]` | 2~5 个关键词标签，add 时可选 |

```yaml
# 存储效果
---
id: 2407011430225
tags:
  - 用户偏好
  - 饮食习惯
  - 素食
---
- [2024-07-01] ★★★☆☆ 用户偏好素食，不吃红肉
```

### Phase 3：搜索召回优化（1~2 天）

**现状**：倒排索引只爬正文 `- [` 行，不爬 tags
**目标**：tags 进索引 + 混合评分排序

| 改进 | 说明 |
|------|------|
| 倒排索引扩展 | 解析 frontmatter tags 加入倒排（特殊行号 -1 标记 tag 命中）|
| 搜索排序加权 | tag 命中结果 base_score + 0.3 提升 |
| tags_filter 过滤 | search 可选参数，只返回含指定 tags 的记忆 |

#### 召回率估计

| 场景 | 当前 | 加 tags 后 | 提升 |
|------|------|-----------|------|
| 精确关键词匹配 | 高 | 高 | ~0% |
| 同义词/近义词 | 中 | 高 | +20~40% |
| 抽象标签检索 | 低 | 高 | +50~80% |
| 跨标签聚类 | 低 | 高 | +60~90% |
| 多条件组合 | 低 | 高 | +40~70% |

---

## 3. 影响范围

| 项目 | 兼容性 |
|------|--------|
| 已存储记忆文件 | ✅ 完全兼容 |
| 已存储提醒文件 | ✅ 完全兼容 |
| 现有对话历史 | ⚠️ 无法回溯（LLM 自会学习新工具） |
| context.py 文案 | ⚠️ **必须同步修改** |
| ws_server.py char_id | ⚠️ 需扩展 |
| PermissionChain | ✅ 无影响 |

数据迁移：**不需要**。文件格式不变，tags 从空列表开始。

---

## 4. 风险矩阵

| # | 风险 | 等级 | 应对 |
|---|------|------|------|
| 1 | context.py 漏改 → LLM 调不存在的 task 操作 | 🟡 中 | Phase 1 必须同步修改 |
| 2 | ReminderTool char_id 未设置 → 默认 ario | 🟡 中 | ws_server 同时设置两个工具 |
| 3 | 倒排索引构建增加 frontmatter 解析开销 | 🟡 中 | >1000 文件时考虑增量构建 |
| 4 | LLM 传入过多 tag（>10 个） | 🟢 低 | description 中约束 2~5 个 |
| 5 | embedding 向量检索不实现 | 🟢 低 | tags 已覆盖主要场景 |
| 6 | 旧的 task 操作残留参数未清理 | 🟡 中 | 解耦后全面审查 MemoryTool parameters |

---

## 5. 待补全功能

### 5.1 参数审查

解耦后需全面检查 MemoryTool 的 `parameters.properties`，清理为 task 操作遗留的参数（如 `task_action`, `trigger_at`, `repeat` 等），确保 schema 干净。

### 5.2 编辑功能扩展

当前 MemoryTool 的 `edit` 操作只能编辑正文内容（`old_string` → `new_string`），但以下元数据无法编辑：

| 元数据 | 当前 | 目标 |
|--------|------|------|
| 文件命名 | 已改为 `标题.md`（去掉了 `[类型]` 前缀），类型在 frontmatter | ✅ 已解决 |
| `importance`（重要度） | 仅 add 时设置，edit 无法修改 | 加 `importance` 参数到 edit 操作 |
| `tags`（标签） | 新增中才有 | 加 `tags` 参数到 edit 操作 |

**edit 操作新增参数**：
```json
{
  "importance": {
    "type": "integer",
    "description": "更新重要度 1-5（edit 可选）"
  },
  "tags": {
    "type": "array",
    "items": {"type": "string"},
    "description": "覆盖更新标签列表（edit 可选）"
  }
}
```

### 5.3 merge 操作兼容 tags

当前 `merge` 操作合并多个记忆文件到目标文件时，tags 处理逻辑：

| 场景 | 方案 |
|------|------|
| 源文件和目标文件都有 tags | 取并集去重 |
| 只有部分源文件有 tags | 合并所有非空 tags |
| 所有文件 tags 均为空 | 结果 `tags: []` |

**实现**：在 `_merge_memories` 中，对所有源文件的 frontmatter.tags 做 `set.union`，写入目标文件。

---

## 6. 实施步骤

```
Phase 1 ──┬── 1. 新建 ReminderTool (reminder_tool.py)
           ├── 2. 修改 MemoryTool 移除 task
           ├── 3. 注册到 main.py
           ├── 4. ws_server 增加 set_char_id
           └── 5. context.py 文案修改

Phase 2 ──┬── 1. add 操作增加 tags 参数
           └── 2. _add_memory 写入 frontmatter

Phase 3 ──┬── 1. 倒排索引扩展（索引 tags）
           ├── 2. 搜索排序 tag 加权
           └── 3. tags_filter 过滤参数
```

3 个 Phase 无依赖关系，可独立交付。建议先做 Phase 1（不涉及搜索逻辑，风险最低），Phase 2+3 可并行。

---

## 📚 成员产出索引

- gstack-product-reviewer（产品官）原始产出：对话内消息
- gstack-investigator（排障手）原始产出：对话内消息

---

> 本方案由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
