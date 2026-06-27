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
capture_screenshot("page.png")

# 提取内容
text = extract_text()
print(text)

# 滚动
scroll_to_bottom()

# 点击元素
click_element("#search-button")

# 获取页面信息
print(get_current_url())
print(get_page_title())
```

---

## 方式二：CLI script 模式（长流程，推荐）

适合多步、可复用的操作。用 bash 调 `python -m core.tools.browser.cli`：

```bash
python -m core.tools.browser.cli goto_url https://www.baidu.com
python -m core.tools.browser.cli wait_for_load
python -m core.tools.browser.cli capture_screenshot baidu_home.png
python -m core.tools.browser.cli search_baidu "AIGEME"
python -m core.tools.browser.cli wait_for_load
python -m core.tools.browser.cli click_result 0
python -m core.tools.browser.cli wait_for_load
python -m core.tools.browser.cli extract_text
```

或者直接执行多行脚本：

```bash
python -m core.tools.browser.cli <<'EOF'
goto_url https://www.baidu.com
wait_for_load
capture_screenshot baidu_home.png
search_baidu "AIGEME"
wait_for_load
click_result 0
wait_for_load
extract_text
EOF
```

---

## 所有可用函数

| 函数 | 说明 |
|------|------|
| `goto_url(url)` | 导航到 URL |
| `wait_for_load()` | 等待页面加载 |
| `search_baidu(keyword)` | 百度搜索 |
| `capture_screenshot(path)` | 截图保存 |
| `extract_text()` | 提取页面文字 |
| `scroll_to_bottom()` | 滚动到底部 |
| `click_element(selector)` | 点击 CSS 选择器 |
| `click_result(index)` | 点击搜索结果的第 index 条 |
| `get_current_url()` | 获取当前 URL |
| `get_page_title()` | 获取页面标题 |
| `switch_tab(index)` | 切换到第 index 个标签页 |
| `close_tab()` | 关闭当前标签页 |
| `go_back()` | 后退 |
| `go_forward()` | 前进 |
| `refresh()` | 刷新页面 |
| `set_download_dir(path)` | 设置下载目录 |
| `download_file(url)` | 下载文件 |

---

## 注意事项

1. 所有函数都会自动打开浏览器（如果尚未打开）
2. 截图默认保存到 `screenshots/` 目录
3. `script` 模式支持多行 Python 代码，变量会保留
4. 多个 `browser_execute` 调用共享同一个浏览器会话
