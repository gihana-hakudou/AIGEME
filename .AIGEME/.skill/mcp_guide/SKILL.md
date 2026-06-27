---
name: mcp_guide
description: MCP 服务器配置与使用指南 — 添加/更新/删除 MCP 服务器、管理工具列表
version: 1.0.0
author: AIGEME
trigger: 用户需要配置或使用 MCP 服务器、问 MCP 相关操作时
---

# MCP 服务器配置与使用指南

MCP（Model Context Protocol）让 LLM 通过标准协议连接外部工具服务器，扩展 Agent 能力。

---

## 1. MCP 工具概览

AIGEME 提供 4 个 MCP 管理工具：

| 工具 | 用途 |
|------|------|
| `mcp_add_server` | 添加新 MCP 服务器 |
| `mcp_update_server` | 修改已有服务器配置 |
| `mcp_delete_server` | 删除服务器配置 |
| `mcp_list_servers` | 列出所有服务器 |

---

## 2. 添加 MCP 服务器（mcp_add_server）

```json
{
  "id": "unique-server-id",
  "name": "显示名称",
  "description": "可选描述（最多256字符）",
  "transport": "stdio | sse | streamable_http",
  "config": {
    // stdio:
    "stdio": { "command": "npx", "args": [...], "env": {} },
    // sse:
    "sse": { "url": "...", "headers": {} },
    // streamable_http:
    "streamable_http": { "url": "...", "headers": {} }
  },
  "enabled": true
}
```

**命令白名单：** `npx`, `uvx`, `python3`, `node`, `python`, `dotnet`, `java`

---

## 3. 更新配置（mcp_update_server）

支持 patch 语义：只传需要改的字段。

- `name` / `description` 修改后立即生效（热加载）
- `config` / `transport` 修改后需要重启

---

## 4. 删除（mcp_delete_server）

软删除：标记为已删除，保留 30 天后由管理员清理。

---

## 5. 典型场景

**添加 GitHub MCP 服务器：**

```json
{
  "id": "github",
  "name": "GitHub API",
  "transport": "stdio",
  "config": {
    "stdio": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "ghp_xxx" }
    }
  }
}
```

> 注意：GITHUB_TOKEN 需要写入 local.yaml 的 env 字段，重启生效。
