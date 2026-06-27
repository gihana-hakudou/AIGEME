---
name: create_character
description: 创建新角色。创建 soul.md、identity.md、expressions.yaml、立绘目录 和 config.yaml
version: 1.0.0
author: AIGEME
trigger: 用户要求"创建角色"、"新增角色"、"添加角色"时
parameters:
  - name: char_id
    type: string
    description: 角色唯一 ID，如 "ario"、"luna"。将作为目录名和配置 id
    required: true
  - name: char_name
    type: string
    description: 角色显示名称，如 "Ario"、"Luna"
    required: true
  - name: personality
    type: string
    description: 角色性格描述
    required: true
---

## 工作流程

执行以下步骤来完成角色创建：

### 第一步：创建角色目录

```bash
mkdir -p character/{char_id}
mkdir -p tachi-e/{char_id}
```

### 第二步：创建 soul.md

soul.md 是角色的核心设定文件，决定了角色的行为方式和回复风格。

模板：

```markdown
# {角色名}

## 你是谁
- 你是 {角色名}，一个 {性格描述}
- 你的设定是 {核心设定}
- 存在的意义是 {存在的意义}

## 对话风格
- 你如何说话
- 喜欢用什么语气
- 有什么口头禅或习惯

## 对话规则
- 对用户的称呼
- 对用户的态度
- 回复长度偏好
- 其他约束
```

### 第三步：创建 identity.md

identity.md 是角色的身份信息卡片。

模板：

```markdown
# {角色名}

## 基本信息
- 年龄：
- 性别：
- 职业：
- 性格：

## 背景故事
（角色的背景故事）

## 外观特征
（角色的外貌描述）
```

### 第四步：创建 expressions.yaml

```yaml
# 立绘表情映射
# key: 表情名（在 soul.md 中引用）
# value: 实际文件路径（相对于立绘目录）
expressions:
  default: neutral.png
  happy: happy.png
  sad: sad.png
  angry: angry.png
  surprised: surprised.png
  thinking: thinking.png
  shy: shy.png
  sigh: sigh.png
  troubled: troubled.png
```

### 第五步：创建 config.yaml

```yaml
name: {char_name}
id: {char_id}
description: "{一句话描述}"
skills:
  - skill_name_1
  - skill_name_2
tachie_dir: "tachi-e/{char_id}"
speak_weight: 1.0
speak_weight_label: "normal"
```

### 第六步：注册到 settings.yaml（如果系统需要）

```bash
# 在 local.yaml 或 settings.yaml 中注册
```

### 第七步：准备立绘文件

在 `tachi-e/{char_id}/` 目录下放置立绘 PNG 文件，文件名与 expressions.yaml 中定义的一致。

---

## 产出清单

| 文件 | 路径 | 必需 |
|------|------|------|
| soul.md | `character/{char_id}/soul.md` | ✅ |
| identity.md | `character/{char_id}/identity.md` | ✅ |
| expressions.yaml | `character/{char_id}/expressions.yaml` | ✅ |
| config.yaml | `character/{char_id}/config.yaml` | ✅ |
| 立绘目录 | `tachi-e/{char_id}/` | ✅ |
| 立绘文件 | `tachi-e/{char_id}/*.png` | 至少一张 |
