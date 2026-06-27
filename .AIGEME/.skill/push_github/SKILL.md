---
name: push_github
description: 通过 GitHub Git API 推送文件到仓库。比 MCP push_files 更灵活，无大小限制，支持批量
version: 1.0.0
author: AIGEME
trigger: 需要推送本地文件到 GitHub 仓库时，尤其是在 MCP push_files 不适用（文件大、批量多）的情况下
parameters:
  - name: files
    type: string
    description: 要推送的文件路径列表，用空格分隔
    required: true
  - name: message
    type: string
    description: 提交信息
    required: true
  - name: branch
    type: string
    description: 分支名（默认 main）
    required: false
---

# Push to GitHub — 通用 GitHub 推送脚本

## 为什么不用 MCP push_files

| | MCP push_files | 本脚本 |
|---|---|---|
| 原理 | 我读文件 → 传给 MCP 进程 → MCP 调 GitHub API | 直接调 GitHub API，少一层中转 |
| 大文件 | 受 MCP 消息大小限制 | 无限制（GitHub API 允许最大 100MB） |
| 批量 | 需分批推送 | 一次全推，一个 commit |
| 依赖 | 依赖 MCP 服务器运行正常 | 只依赖 Python 标准库 |

## 前提

GitHub token 已配置（优先顺序）：
1. 环境变量 `GITHUB_TOKEN` 或 `GH_TOKEN`
2. MCP 配置 `.AIGEME/mcp-servers/mcp-servers.json` 中的 `GITHUB_PERSONAL_ACCESS_TOKEN`
3. 运行时 `-t` 参数指定

## 命令行用法

```bash
python scripts/push_github.py <文件1> <文件2> ... -m "提交信息"

# 示例：推核心文件
python scripts/push_github.py core/main.py frontend/chat/js/app.js -m "fix: 立绘路径修复"

# 指定分支和 token
python scripts/push_github.py config.yaml -m "update config" -b dev -t "ghp_xxx"

# 预览不推送
python scripts/push_github.py *.py -m "批量推送" --dry-run
```

## Python 模块用法

```python
from scripts.push_github import push_files

push_files(
    files=[
        {"path": "core/main.py", "content": "..."},
        {"path": "config.yaml", "content": "..."},
    ],
    message="修复立绘路径",
    owner="gihana-hakudou",
    repo="AIGEME",
    branch="main",
)
```

返回 commit SHA。

## 工作流程

1. 确认要推送的文件列表
2. 确认提交信息
3. 调用 `python scripts/push_github.py <文件> -m "信息"`
4. 脚本自动完成：获取最新 commit → 创建 blob → 创建 tree → 创建 commit → 更新引用

## 注意事项

- 支持任意类型文件（二进制、文本均可）
- 路径使用 `/` 分隔，自动从本地相对路径转为仓库路径
- 如果文件不存在会自动跳过并提示
- token 需要有仓库的写入权限
