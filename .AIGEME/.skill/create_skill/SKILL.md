---
name: create_skill
description: 创建新技能的完整流程指南。当用户要求新增一个技能时，按此流程执行。当发现可复用的工作流时也应使用此技能创建技能
version: 1.1.0
author: AIGEME
trigger: 用户要求"创建技能"、"新增技能"、"添加技能"时
parameters:
  - name: skill_name
    type: string
    description: 技能的唯一标识名（目录名），如 "find_file"、"code_review"
    required: true
  - name: description
    type: string
    description: 技能的简要描述（一行），注入 prompt 时 LLM 靠它理解用途
    required: true
  - name: workflow_steps
    type: string
    description: 技能的工作步骤描述，描述了执行该技能的完整流程和注意事项
    required: true
---

## 技能结构

```
.AIGEME/.skill/{skill_name}/
  └── SKILL.md       # 技能文档
```

---

## 创建流程

### 第一步：创建技能目录

```bash
mkdir -p .AIGEME/.skill/{skill_name}
```

### 第二步：编写 SKILL.md

内容结构如下：

```markdown
---
---
name: {skill_name}
description: {一句话描述}
version: 1.0.0
author: AIGEME
trigger: {触发条件，什么情况下使用该技能}
---

# {技能名称}

（技能文档正文...）
```

**编写要点：**

1. 清晰说明技能的使用场景和触发条件
2. 列出具体的步骤和操作指南
3. 包含可执行的命令或代码示例
4. 注意事项和边界说明
5. 可选的参数说明

---

## 示例

参考已有技能：
- `browser-control` — 浏览器自动化
- `code_dev` — 编码开发工作流
- `create_character` — 创建角色
- `mcp_guide` — MCP 服务器配置指南
