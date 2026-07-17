---
name: doubao-image-search
description: 通过豆包（Doubao）以图搜图，识别番剧、角色、场景等图片来源
version: 2.0.0
author: AIGEME
trigger: 需要识别图片来源、以图搜图、寻找番剧/角色名时使用
---

# 豆包以图搜图技能

通过浏览器控制豆包网页，利用**系统级剪贴板 + 模拟按键**上传图片并进行识别。

## 核心原理

豆包前端的上传机制依赖**粘贴事件**（Ctrl+V）。浏览器沙箱内的 CDP/JS 模拟按键会被拦截。  
必须通过**操作系统级别**的按键模拟（win32api）才能绕过限制。

## 前置条件

- 豆包页面已打开：`https://www.doubao.com/chat/`
- 图片文件路径（建议短路径 `C:/temp/`）
- 依赖：`pywin32` + `Pillow`

## 脚本位置

所有脚本存放在 `scripts/` 目录下：

| 脚本 | 功能 |
|------|------|
| `set_clipboard.py` | 将图片文件写入系统剪贴板（BMP格式） |
| `paste_img.py` | 查找豆包窗口，发送 Ctrl+V |
| `send_enter.py` | 查找豆包窗口，发送 Enter |
| `send_to_doubao.py` | **一键完成**：设剪贴板→粘贴图片→贴文字→发送 |

## 完整工作流

### 第一步：打开豆包页面

在 browser_execute 中打开并聚焦输入框：

```python
goto_url("https://www.doubao.com/chat/")
wait(6)

# 聚焦输入框
js("""
var ta = document.querySelectorAll('textarea')[0];
ta.focus();
ta.click();
'focused';
""")
```

### 第二步：将图片复制到短路径

```bash
cp "{原始图片路径}" "C:/temp/upload_img.jpg"
```

### 第三步：一键发送到豆包（推荐）

```bash
python G:/AIGEME/.AIGEME/.skill/doubao-image-search/scripts/send_to_doubao.py C:/temp/upload_img.jpg "帮我识别这张图是什么漫画，包括作品名和角色名"
```

此脚本会自动完成：设置剪贴板图片 → 查找豆包窗口 → Ctrl+V粘贴图片 → 粘贴提示文字 → Enter发送。

### 第四步：等待并读取结果

```python
wait(15)  # 豆包需要时间搜索+生成回复
capture_screenshot("doubao_result.png")
```

## 分步操作（如需要手动控制）

如果一键脚本不适用，可按顺序执行：

### 设置剪贴板图片

```bash
python G:/AIGEME/.AIGEME/.skill/doubao-image-search/scripts/set_clipboard.py
```

（注意：当前脚本写死了 `C:/temp/upload_img1.jpg`，如需改路径需手动编辑）

### 粘贴图片到豆包

```bash
python G:/AIGEME/.AIGEME/.skill/doubao-image-search/scripts/paste_img.py
```

### 验证是否粘贴成功

通过截图检查页面是否有 **"解释图片→"** 快捷按钮。有这个按钮就证明图片上传成功。

### 输入文字并发送

先通过 JS 填入文字：

```python
js("""
var ta = document.querySelectorAll('textarea')[0];
var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
nativeSetter.call(ta, '帮我识别这张图是什么漫画，包括作品名和角色名');
ta.dispatchEvent(new Event('input', {bubbles: true}));
""")
```

再用 OS 级 Enter 发送：

```bash
python G:/AIGEME/.AIGEME/.skill/doubao-image-search/scripts/send_enter.py
```

## 判断技巧

上传成功后，豆包输入框上方会出现 **"解释图片→"** 快捷按钮。  
这是判断图片是否成功粘贴的关键标志。

## 故障排查

| 问题 | 可能原因 | 解决方法 |
|------|---------|---------|
| 找不到豆包窗口 | 窗口标题不匹配 | 检查窗口标题是否含"豆包"或"Doubao" |
| 粘贴后无"解释图片"按钮 | 图片未写入剪贴板 | 检查 `set_clipboard.py` 中的图片路径 |
| SendKeys 脚本无输出 | SetForegroundWindow 失败 | 手动点击豆包窗口使其获得焦点再运行 |
| 豆包回复慢 | 图片复杂/搜索量大 | 等待 15-30 秒后再截图检查 |
| CDP 上传文件不生效 | 豆包前端不吃 CDP | **不要用 CDP**，改用本技能的 OS 级模拟方案 |

## 版本历史

- v2.0.0 (2026-07-16): 重写为系统级剪贴板+模拟按键方案，废弃 CDP 上传方式
- v1.0.0 (2026-07-15): 初始版本，使用 CDP 上传文件
