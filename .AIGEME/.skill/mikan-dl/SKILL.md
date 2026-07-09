---
name: mikan-dl
description: BT种子/磁力链/流媒体下载器，支持断点续传、后台下载、进度查询
version: 1.0.0
author: AIGEME
trigger: 用户要求下载种子、磁力链、视频时
related_skills:
  - mikan-rss: 蜜柑计划资源搜索，可配合使用进行番剧下载
parameters:
  - name: url_or_file
    type: string
    description: 种子文件路径、磁力链接或流媒体URL
    required: true
  - name: output_dir
    type: string
    description: 保存目录，默认~/Downloads
    required: false
---

# mikan_dl — 轻量级下载器

通过 Bash 工具运行 `scripts/mikan_dl.py`，下载种子、磁力链或流媒体。

## 脚本路径

```
.AIGEME/.skill/mikan-dl/scripts/mikan_dl.py
```

## 运行方式

```bash
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py <子命令> [参数]
```

## 可用命令

| 命令 | 用途 |
|------|------|
| `add <url_or_file> [-o DIR] [-n NAME]` | 添加下载任务（立即返回） |
| `list` | 查看所有任务状态 |
| `status <task_id>` | 查看任务详情 |
| `pause <task_id>` | 暂停下载 |
| `resume <task_id>` | 恢复下载 |
| `remove <task_id> [--delete-files]` | 删除任务 |
| `daemon` | 启动后台监控（调试用） |

## 使用场景

### 下载种子文件

```bash
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py add "path/to/torrent.torrent" -o "D:/Downloads/Anime"
```

### 下载磁力链

```bash
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py add "magnet:?xt=urn:btih:..." -o "D:/Downloads/Anime"
```

### 下载流媒体（YouTube等）

```bash
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py add "https://www.youtube.com/watch?v=..." -o "D:/Downloads/Video"
```

### 查看下载进度

```bash
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py list
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py status <task_id>
```

### 暂停/恢复下载

```bash
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py pause <task_id>
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py resume <task_id>
```

### 删除任务

```bash
# 仅删除任务，保留文件
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py remove <task_id>

# 删除任务和文件
python .AIGEME/.skill/mikan-dl/scripts/mikan_dl.py remove <task_id> --delete-files
```

## 特性

- **非阻塞设计**：`add` 命令立即返回，不阻塞agent
- **断点续传**：session状态自动保存，重启后可恢复下载
- **多源支持**：种子、磁力链、流媒体URL
- **进度跟踪**：实时显示速度、进度、剩余时间
- **后台运行**：支持daemon模式持续监控

## 注意事项

1. **依赖**：需要安装 `libtorrent` 和 `yt-dlp`（已预装）
2. **网络**：P2P下载需要开放端口（默认6881-6891）
3. **磁盘缓存**：默认256MB，适合千兆网络
4. **断点续传**：退出时自动保存状态，下次启动自动恢复
5. **路径**：支持自动创建输出目录，路径中的引号会自动清理