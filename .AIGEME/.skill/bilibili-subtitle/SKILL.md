---
---
name: bilibili-subtitle
description: 提取B站视频的AI自动生成字幕或CC字幕，支持导出带时间戳的文本和纯文本两种格式
version: 1.0.0
author: AIGEME
trigger: 用户要求提取/下载/获取B站视频的字幕、文案、转录文本时
---

# Bilibili 字幕提取

从B站视频中提取字幕文本（AI自动生成字幕或用户CC字幕），导出为可读文本。

## 前置条件

- **浏览器必须已登录B站账号** — B站字幕API需要登录cookie
- 如果API返回 `"need_login_subtitle": true` 且 `"subtitles": []`，说明未登录，字幕不可用

## 提取流程

### Step 1: 获取视频 AID 和 CID

通过B站开放API获取视频基础信息，不需要登录：

```python
# browser_execute
goto_url("https://api.bilibili.com/x/web-interface/view?bvid={BVID}")
wait_for_load(10)
wait(1)
body = js("document.body.innerText")
# 从返回JSON中提取 data.aid 和 data.cid
```

BVID 就是视频URL中的 `BV` 开头那串，如 `BV124TD6GECb`。

### Step 2: 获取字幕列表

调用播放器API（**需要浏览器登录态**）：

```python
# browser_execute
goto_url("https://api.bilibili.com/x/player/wbi/v2?aid={AID}&cid={CID}")
wait_for_load(10)
wait(1)
body = js("document.body.innerText")
```

从返回JSON的 `data.subtitle.subtitles` 数组中提取字幕信息：

```json
{
  "subtitles": [
    {
      "id": 2056500367209929728,
      "lan": "ai-zh",              // 语言标识：ai-zh=AI中文, zh=用户CC字幕
      "lan_doc": "中文",
      "subtitle_url": "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/..."
    }
  ]
}
```

- `lan: "ai-zh"` = AI自动生成字幕
- `lan: "zh"` = 用户上传的CC字幕
- `subtitle_url` 以 `//` 开头，需要补 `https:` 前缀

### Step 3: 下载字幕JSON

```python
# browser_execute
subtitle_url = "https:" + subtitle_url_from_step2
goto_url(subtitle_url)
wait_for_load(10)
wait(1)
body = js("document.body.innerText")
```

返回的JSON结构：

```json
{
  "body": [
    {"from": 0.04, "to": 3.12, "content": "最近呢有一个视频生成的玩法很火啊"},
    {"from": 3.12, "to": 5.9, "content": "就是先用blender或者其他的3D工具啊"}
  ]
}
```

- `from` = 开始时间（秒）
- `to` = 结束时间（秒）
- `content` = 字幕文本

### Step 4: 解析与保存

解析 `body` 数组，组装为两种格式：

**带时间戳格式：**
```
[0.0s - 3.1s] 最近呢有一个视频生成的玩法很火啊
[3.1s - 5.9s] 就是先用blender或者其他的3D工具啊
```

**纯文本合并格式（适合看全文）：**
```
最近呢有一个视频生成的玩法很火啊
就是先用blender或者其他的3D工具啊
```

保存到用户指定的路径，或默认保存到视频同目录。

## 一键提取脚本（推荐）

用一个 `browser_execute` 调用完成三步：

```python
# 1. 获取aid和cid
bvid = "BV124TD6GECb"
goto_url(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
wait_for_load(10)
wait(1)
info = js("document.body.innerText")
import json
data = json.loads(info)
aid = data["data"]["aid"]
cid = data["data"]["cid"]

# 2. 获取字幕列表
goto_url(f"https://api.bilibili.com/x/player/wbi/v2?aid={aid}&cid={cid}")
wait_for_load(10)
wait(1)
sub_info = js("document.body.innerText")
sub_data = json.loads(sub_info)
subs = sub_data["data"]["subtitle"]["subtitles"]

if not subs:
    print("该视频没有可用字幕（未登录或UP主未上传字幕）")
else:
    # 优先选择AI字幕(ai-zh)，其次用户字幕(zh)
    sub = next((s for s in subs if s["lan"] == "ai-zh"), subs[0])
    sub_url = "https:" + sub["subtitle_url"]
    
    # 3. 下载字幕内容
    goto_url(sub_url)
    wait_for_load(10)
    wait(1)
    sub_content = js("document.body.innerText")
    sub_json = json.loads(sub_content)
    
    # 4. 格式化输出
    lines = []
    plain_lines = []
    for item in sub_json["body"]:
        start = item["from"]
        end = item["to"]
        text = item["content"]
        lines.append(f"[{start:.1f}s - {end:.1f}s] {text}")
        plain_lines.append(text)
    
    timestamp_text = "\\n".join(lines)
    plain_text = "\\n".join(plain_lines)
    
    print(f"共 {len(lines)} 条字幕")
    print("=" * 40)
    print(timestamp_text)
```

## CLI 脚本方式（推荐）

脚本路径：`.AIGEME/.skill/bilibili-subtitle/scripts/bili_subtitle.py`

运行方式（Bash工具中执行）：

```bash
# 基本用法：提取字幕打印到终端
python .AIGEME/.skill/bilibili-subtitle/scripts/bili_subtitle.py BV124TD6GECb

# 保存到文件
python .AIGEME/.skill/bilibili-subtitle/scripts/bili_subtitle.py BV124TD6GECb --output subtitle.txt

# 只输出纯文本（不带时间戳）
python .AIGEME/.skill/bilibili-subtitle/scripts/bili_subtitle.py BV124TD6GECb --plain

# 指定字幕语言（默认 ai-zh=AI中文，zh=用户CC字幕）
python .AIGEME/.skill/bilibili-subtitle/scripts/bili_subtitle.py BV124TD6GECb --lang zh
```

### 脚本执行流程

```
[1/4] 获取视频信息: BV124TD6GECb
  ✅ 《LTX 2.3 镜头控制全教学...》
     AID: 116870623595919, CID: 39690504418
[2/4] 获取字幕列表...
  ✅ 找到 1 条字幕:
     - [AI中文] 中文
[3/4] 下载字幕 (AI中文)...
  ✅ 下载成功，共 63 条字幕
[4/4] 格式化字幕...
  ✅ 提取完成！共 63 条字幕
```

## browser_execute 方式（短操作/一次性）

适合不想切 CLI，直接在对话中用 `browser_execute` 工具完成：

```python
bvid = "BV124TD6GECb"

# 1. 获取aid和cid
goto_url(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
wait_for_load(10)
wait(1)
info = js("document.body.innerText")
import json
data = json.loads(info)
aid = data["data"]["aid"]
cid = data["data"]["cid"]

# 2. 获取字幕列表
goto_url(f"https://api.bilibili.com/x/player/wbi/v2?aid={aid}&cid={cid}")
wait_for_load(10)
wait(1)
sub_info = js("document.body.innerText")
sub_data = json.loads(sub_info)
subs = sub_data["data"]["subtitle"]["subtitles"]

if not subs:
    print("该视频没有可用字幕（未登录或UP主未上传字幕）")
else:
    sub = next((s for s in subs if s["lan"] == "ai-zh"), subs[0])
    sub_url = "https:" + sub["subtitle_url"]
    
    goto_url(sub_url)
    wait_for_load(10)
    wait(1)
    sub_content = js("document.body.innerText")
    sub_json = json.loads(sub_content)
    
    lines = []
    for item in sub_json["body"]:
        start = item["from"]
        end = item["to"]
        text = item["content"]
        lines.append(f"[{start:.1f}s - {end:.1f}s] {text}")
    
    print(f"共 {len(lines)} 条字幕")
    print("\\n".join(lines))
```

## 依赖

- 项目 venv（Python 标准库，无外部依赖）
- Browser 工具（`core.tools.browser.cli`）

## 注意事项

1. **必须登录**：B站字幕API依赖登录cookie。浏览器需要已登录B站账号。
2. **没有登录态时**：API返回 `"need_login_subtitle": true` 且 `subtitles: []`。
3. **字幕类型**：`ai-zh` = AI自动生成（大部分视频有），`zh` = 用户上传CC字幕（少数视频有）。
4. **无字幕的视频**：部分视频没有开启AI字幕功能，API会返回空列表。
5. **付费视频**：付费/大会员专属视频需要额外处理。
6. **压制字幕**：嵌入视频画面的硬字幕（非独立字幕流）无法通过API提取。
