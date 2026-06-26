---
name: setup_web_search
description: 引导用户注册 Tavily 并配置联网搜索 API Key，写入 local.yaml
version: 1.0.0
author: AIGEME
trigger: 用户需要配置联网搜索、没有搜索 Key、需要注册 Tavily、配置 web_search
parameters:
  - name: confirm_write
    type: boolean
    description: 是否已获得用户确认写入配置文件
    required: false
---

## 功能

帮用户完成 Tavily 注册 → 获取 API Key → 配置 local.yaml 的全流程。

注意：Tavily 注册页有验证码/机器人检测，需要用户手动操作验证部分，AI 负责导航和引导。

## 流程

### 第一步：打开 Tavily 注册页

用浏览器打开注册页面，截图给用户看当前状态。

```python
# browser_execute 代码
goto_url("https://tavily.com/")
wait_for_load()
# 提取页面文字和标题
page = page_info()
text = js("document.body.innerText")
print(f"标题: {page['title']}")
print(f"页面文字:\n{text[:1500]}")
```

### 第二步：引导用户注册

根据页面内容判断当前处于哪个阶段，引导用户操作：

**场景 A：在首页（有 "Get Started" 或 "Sign Up" 按钮）**
- 告诉用户点击 "Get Started" 或右上角的 "Sign Up"
- 说明：注册页有机器人验证，需要用户手动操作

**场景 B：在登录页（Log in 页面）**
- 告诉用户点击 "Don't have an account? Sign up" 链接切换到注册页
- 说明：需要用户手动点击

**场景 C：在注册页（填邮箱）**
- 引导用户填写邮箱地址
- 说明：点击 Continue 后会发送验证码到邮箱
- 验证码环节需要用户手动操作

**场景 D：验证码/人机验证页面**
- 告知用户：这是机器人检测，无法自动完成
- 引导用户手动完成验证

**场景 E：在 Dashboard（已登录）**
- 引导用户找到 "API Keys" 或 "Generate Key" 按钮
- 点击生成 Key，复制那串 `tvly-dev-xxx` 格式的字符串

### 第三步：配置 local.yaml

获得 Key 后，询问用户是否确认写入配置文件 `.AIGEME/local.yaml`。

用户确认后执行：

```python
# document 操作，读取现有 local.yaml
local_yaml = document(operation="read", path=".AIGEME/local.yaml")

# 如果已有 web_search 配置，更新 api_key
# 如果没有，追加 web_search 配置段
```

写入内容示例（单个 Key）：
```yaml
web_search:
  api_key: tvly-dev-用户的Key
  backend: tavily
  max_results: 5
```

如果用户注册了多个 Key，可配置轮换使用：
```yaml
web_search:
  api_keys:
    - tvly-dev-Key1
    - tvly-dev-Key2
    - tvly-dev-Key3
  backend: tavily
  max_results: 5
```

用户如果担心单个 Key 额度不够，可以建议多注册几个账号，把 Key 都放进 `api_keys` 列表里。系统会按顺序自动轮换，超出配额自动换下一个。

如果不确认写入，则告知用户手动编辑 `.AIGEME/local.yaml` 文件。

### 第四步：验证

配置完成后，使用 `web_search` 工具进行一次搜索测试，确认 Key 有效。

```python
# 搜索测试
search_result = web_search(query="test", max_results=1)
print(f"搜索测试结果: {search_result}")
```

## 注意

1. 注册过程中遇到验证码/人机验证时，必须告知用户手动操作，不能尝试绕过
2. 写入 local.yaml 前必须获得用户明确同意
3. 如果用户不愿意注册 Tavily，告知可以使用浏览器搜索作为替代方案
4. Tavily 免费额度每月 1000 次搜索，个人日常使用足够
5. 如果用户担心额度不够且邮箱充裕，可以注册多个账号获取多个 Key，填入 `api_keys` 列表，系统会自动轮换使用。理论上 Key 足够多就可以无限白嫖

### 配置后是否需要重启

配置写入 local.yaml 后**通常不需要重启后端**，系统会在下一次调用搜索时自动读取最新配置。

但如果搜索功能报错（Key 无效或未配置），建议重启后端：
1. 关掉当前终端（Ctrl+C）
2. 重新双击 `start.bat`
5. 如果用户担心额度不够且邮箱充裕，可以注册多个账号获取多个 Key，填入 `api_keys` 列表，系统会自动轮换使用。理论上 Key 足够多就可以无限白嫖
