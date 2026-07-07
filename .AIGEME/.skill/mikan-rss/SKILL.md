---
name: mikan-rss
description: 搜索蜜柑计划（Mikan Project）番剧资源、管理追番列表、下载种子文件、导出种子列表
version: 1.2.0
author: AIGEME
trigger: 用户要求搜索/下载/追番动漫资源时
parameters:
  - name: keyword
    type: string
    description: 番剧搜索关键词
    required: false
  - name: page
    type: integer
    description: 搜索结果页码，默认第1页
    required: false
  - name: group_id
    type: integer
    description: 字幕组ID，筛选特定字幕组的资源
    required: false
  - name: bangumi_id
    type: string
    description: 蜜柑番剧ID，直接通过RSS接口获取而非搜索（可绕过搜索API限制）
    required: false
  - name: episode
    type: string
    description: 要下载的集数，如 "01"、"38"。不指定则默认下载最新集
    required: false
  - name: all
    type: boolean
    description: 下载全集/批量包（无集数的条目）
    required: false
---

# Mikan RSS — 蜜柑计划搜索与追番工具

通过 Bash 工具运行 `scripts/mikan_cli.py`，搜索番剧、管理追番、下载种子、导出列表。

## 脚本路径

```
.AIGEME/.skill/mikan-rss/scripts/mikan_cli.py
```

## 运行方式

Bash 工具中直接使用 `python` 加相对路径：

```bash
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py <子命令> [参数]
```

> `python` 会被 `bash_tools.py` 自动解析为项目 venv 的 Python，无需指定完整路径。

## 可用命令

| 命令 | 用途 |
|------|------|
| `season` | 查看当前季番列表（按星期分组，含 bangumiId） |
| `search <关键词> [--page N] [--group-id G] [--limit N]` | 搜索番剧，表格展示（含番剧名列+字幕组ID） |
| `list-groups <关键词>` | 列出该番剧的所有字幕组及对应字幕组ID |
| `export <关键词> [--page N] [--group-id G] [--output FILE]` | 导出种子列表到文本文件（含番剧名） |
| `download <关键词> [--group-id G] [--episode N] [--all] [--dir DIR]` | 下载 .torrent 种子文件 |
| `subscribe add <番剧名> --group-id G --group-name NAME` | 添加追番记录 |
| `subscribe list` | 查看所有追番 |
| `subscribe remove <番剧名>` | 移除追番 |
| `check` | 检查所有追番的最新更新 |

## 使用场景

### 查看当前季番

```bash
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py season
```
按星期分组展示当前季番，每部番显示名称和 bangumiId。

### 搜索资源

```bash
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py search "葬送的芙莉莲" --limit 10
```

结果表格包含：序号、字幕组、集数、画质、大小、日期、番剧名。
模糊搜索时能一眼区分不同番剧。

### 按番剧ID搜索（推荐，绕过搜索API限制）

当搜索API搜不到时（如新番刚上线），可直接用 `season` 命令查到 bangumiId 后直连：

```bash
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py search --bangumi-id 3981 --group-id 615
```

### 按字幕组筛选

```bash
# 列出所有字幕组（同时显示字幕组ID）
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py list-groups "葬送的芙莉莲"

# 按字幕组 ID 搜索
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py search "葬送的芙莉莲" --group-id 583
```

### 下载种子

```bash
# 下载最新一集
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py download "葬送的芙莉莲" --group-id 583

# 通过番剧ID下载（绕过搜索API限制）
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py download --bangumi-id 3981 --group-id 615 --episode 1

# 下载指定集数
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py download "葬送的芙莉莲" --group-id 583 --episode 38

# 下载全集/批量包（无集数的条目，如 BDRip 全季包）
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py download "魔都精兵的奴隶" --group-id 1212 --all
```

### 管理追番

```bash
# 添加
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py subscribe add "葬送的芙莉莲" --group-id 583 --group-name "喵萌奶茶屋"

# 查看
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py subscribe list

# 检查更新
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py check

# 移除
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py subscribe remove "葬送的芙莉莲"
```

### 导出列表

```bash
# 导出种子列表到文件（每行包含字幕组、番剧名、集数、大小、链接）
python .AIGEME/.skill/mikan-rss/scripts/mikan_cli.py export "葬送的芙莉莲" --output seeds.txt
```

## 注意事项

1. **中文编码**：脚本已处理 Windows GBK 终端，输出中文不会乱码
2. **网络**：需要能访问 mikanani.me（可能需要代理）
3. **Python**：纯标准库无外部依赖，项目 venv 即可运行
4. **追番存储**：`scripts/mikan_subscriptions.json`，自动跟随脚本位置
5. **路径兼容**：全部使用相对路径，不同电脑无需修改
6. **list-groups 的 ID 列**：部分联合字幕组可能匹配不到 ID，主流单字幕组都能正确显示
