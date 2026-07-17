---
name: browser-control
description: 控制 Chrome 浏览器，支持搜索、导航、截图、提取内容、点击等完整浏览器自动化。短操作用 browser_execute 工具直接执行 Python 代码；长流程/可复用脚本用 bash 调 CLI 的 script 模式
---

# Browser Control — 浏览器控制技能

有两种方式操作浏览器，根据场景选择：

---

## 方式一：browser_execute 工具（短操作，无需确认）

适合单步或几步的简单操作。用工具调用 `browser_execute(code=...)` 直接传 Python 代码：

```python
# 导航
goto_url("https://www.baidu.com")
wait_for_load()

# 搜索
search_baidu("AI 2024")

# 截图
capture_screenshot()               # 默认保存到 .AIGEME/.data/tmp/browser-control/screenshots/
capture_screenshot("custom.png")   # 指定文件名（相对路径自动重定向到同一目录）

# 获取页面信息
info = page_info()
print(f"Title: {info['title']}")
print(f"URL: {info['url']}")

# 提取文本
text = js("document.body.innerText")
print(f"Content length: {len(text)}")

# 滚动
scroll(dy=-300)

# 点击坐标
click_at_xy(500, 300)

# 填充输入框
fill_input("#search", "hello")
```

---

## 方式二：CLI script 模式（长流程，推荐）

适合多步、可复用的操作。用 bash 调 `python -m core.tools.browser.cli`：

```bash
python -m core.tools.browser.cli goto_url https://www.baidu.com
python -m core.tools.browser.cli wait_for_load
python -m core.tools.browser.cli capture_screenshot
python -m core.tools.browser.cli search_baidu "AIGEME"
python -m core.tools.browser.cli wait_for_load
python -m core.tools.browser.cli page_info
python -m core.tools.browser.cli js "document.body.innerText"
```

或者直接执行多行脚本：

```bash
python -m core.tools.browser.cli <<'EOF'
goto_url https://www.baidu.com
wait_for_load
capture_screenshot baidu_home.png
search_baidu "AIGEME"
wait_for_load
page_info
js "document.body.innerText"
EOF
```

---

## 所有可用函数

| 函数 | 说明 |
|------|------|
| `goto_url(url)` | 导航到 URL |
| `search_baidu(keyword)` | 百度搜索 |
| `page_info()` | 获取页面信息（url、标题、文本内容） |
| `capture_screenshot(path)` | 截图保存，返回 {path, data_url} |
| `js(expression)` | 执行 JavaScript 并返回值 |
| `click_at_xy(x, y)` | 点击坐标位置 |
| `fill_input(selector, text)` | 填充输入框 |
| `type_text(text)` | 键入文本 |
| `press_key(key)` | 按键（Enter, Tab 等） |
| `dispatch_key(selector, key)` | 在元素上分派键盘事件 |
| `scroll(x, y)` | 滚动页面（dy=-300 向上滚动） |
| `wait(seconds)` | 等待指定秒数 |
| `wait_for_load(timeout)` | 等待页面加载完成 |
| `wait_for_element(selector)` | 等待元素出现 |
| `wait_for_network_idle(timeout)` | 等待网络空闲 |
| `list_tabs()` | 列出所有标签页 |
| `switch_tab(target)` | 切换标签页 |
| `close_tab(target)` | 关闭标签页 |
| `new_tab(url)` | 新建标签页 |
| `back()` | 后退 |
| `forward()` | 前进 |
| `upload_file(selector, path)` | 上传文件 |
| `http_get(url)` | HTTP GET 请求 |
| `handle_dialog(accept)` | 处理弹窗 |
| `cdp(method, **params)` | 调用 Chrome DevTools Protocol |

---

## 注意事项

1. 所有函数都会自动打开浏览器（如果尚未打开）
2. 截图默认保存到 `.AIGEME/.data/tmp/browser-control/screenshots/` 目录
3. `script` 模式支持多行 Python 代码，变量会保留
4. 多个 `browser_execute` 调用共享同一个浏览器会话
