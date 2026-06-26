# 内嵌 Chromium 方案架构分析

> **分析者**: Bob (Architect)
> **日期**: 2025-06-18
> **项目**: AIGEME Browser Control Module (`core/tools/browser/`)

---

## 0. 当前架构回顾

```
                      ┌─────────────────────────────────────────────┐
                      │                LLM / Agent                  │
                      │   (browser_execute, browser_search, ...)    │
                      └──────────────┬──────────────────────────────┘
                                     │ async tool call
                                     ▼
                      ┌─────────────────────────────────────────────┐
                      │              tools.py (async)               │
                      │   BaseTool.execute() → exec(code, globals)  │
                      └──────────────┬──────────────────────────────┘
                                     │ sync import + call
                                     ▼
                      ┌─────────────────────────────────────────────┐
                      │              manager.py                     │
                      │    BrowserManager (singleton)               │
                      │    start() → subprocess(daemon.py)          │
                      └──────────────┬──────────────────────────────┘
                                     │ helpers.py ← sync IPC
                                     ▼
                      ┌─────────────────────────────────────────────┐
                      │              helpers.py (sync)               │
                      │   _send(req) → IPC socket → daemon.py       │
                      │   page_info(), goto_url(), click_at_xy()... │
                      └──────────────┬──────────────────────────────┘
                                     │ TCP (Win) / UNIX socket (POSIX)
                                     ▼
                      ┌─────────────────────────────────────────────┐
                      │           daemon.py (async CDP)              │
                      │   Daemon.start() → get_ws_url() → CDPClient │
                      │   handle(req) → CDP IPC relay               │
                      │   Event tap: download/dialog/title          │
                      └──────────────┬──────────────────────────────┘
                                     │ CDP WebSocket (ws://127.0.0.1:9222)
                                     ▼
                      ┌─────────────────────────────────────────────┐
                      │         外部 Chrome / Edge / Brave           │
                      │   用户已安装的浏览器，通过 CDP 控制           │
                      │   --remote-debugging-port=9222              │
                      └─────────────────────────────────────────────┘
```

**关键文件及行数**:
| 文件 | 行数 | 职责 |
|------|------|------|
| `daemon.py` | ~557 | CDP daemon, WS 连接, IPC 服务, 事件拦截 |
| `manager.py` | ~148 | 生命周期管理（启动/停止/重启） |
| `helpers.py` | ~687 | 浏览器操作函数（~30+ 个 helper） |
| `cli.py` | ~385 | CLI 命令行接口（动态子命令） |
| `tools.py` | ~310 | LLM Tool 接口（3 个 BaseTool） |
| `_ipc.py` | ~236 | IPC 传输层（TCP/UNIX socket） |

---

## 1. 方案对比表

| 维度 | 当前（外部 Chrome） | 内嵌 Chromium |
|------|-------------------|---------------|
| **安装依赖** | 用户必须预先安装 Chrome/Edge/Brave，且版本需支持 CDP（Chrome 60+） | 无外部依赖。项目首次运行时自动下载，或通过 `playwright install chromium` 一步完成 |
| **下载管理** | 需要复杂的 `Browser.downloadWillBegin` 事件监听 + CDP Runtime `fetch()` 回传方案。Chrome 130+ 已移除 `Browser.setDownloadBehavior` | Playwright 原生 `page.on('download', ...)` 直接获取文件路径。浏览器自动处理下载对话框，无需 CDP hack |
| **配置复杂度** | 高：需自动查找 Chrome 可执行文件（~20 个备选路径），查找/创建用户数据目录，处理 DevToolsActivePort 文件，轮询等待 | 低：Playwright 自动管理所有配置（二进制路径、数据目录、启动参数）。无需查找逻辑（删除 `find_chrome_executable` 和 `_CHROME_PROFILES`） |
| **稳定性** | 中等：用户 Chrome 升级可能 break CDP API；用户手动安装的扩展/配置可能干扰行为；`daemon.py` 中有复杂的 session 重新连接逻辑 | 高：固定的 Chromium 版本（由 Playwright 锁定），CDP API 行为可预测；无外部干扰。版本由 `package.json` / playwright 版本锁定 |
| **用户隔离** | 差：虽然用了独立的 `--user-data-dir`，但用户同时使用同一 Chrome 实例仍可能冲突（GPU/GPU 进程共享） | 优：完全独立的 Chromium 进程，与用户浏览器零接触 |
| **多实例** | 困难：两个 daemon 需要不同的 debugging port（9222/9223/9333），port 冲突管理复杂 | 简单：每个实例独立进程，无 port 冲突——Playwright 自动分配 debugging port 或通过管道通信 |
| **截图/多模态** | 依赖 `Page.captureScreenshot` CDP 调用，已有实现 | 相同能力，Playwright 提供 `page.screenshot()` 封装，代码更简洁。支持全页截图、元素截图 |
| **CDP 兼容性** | 100% CDP 兼容——`helpers.py` 直接发送 CDP 命令 | 轻度方案（中等/轻量）保持相同 CDP 兼容。重度方案（全 Playwright API）需要重写 helpers |
| **二进制体积** | 0（复用用户安装的浏览器） | ~150-200MB（下载到项目或用户目录）。CI/CD 中需要预缓存 |
| **启动速度** | 慢：需要查找可执行文件、创建用户目录、等待 DevTools 端口就绪（~15s 超时） | 快：Playwright 直接启动 Chromium 子进程，管道通信更快（~3-5s 就绪） |
| **跨平台** | 已有完整的三平台查找路径（Windows/Mac/Linux） | Playwright 三平台全支持。二进制下载自动匹配平台 |

---

## 2. 内嵌 Chromium 的优势

### 2.1 彻底解决下载问题（痛点 #1）

当前架构中下载需要：

1. 监听 `Browser.downloadWillBegin` 事件（daemon.py:282-288）
2. 获取下载 URL 和文件名 (suggested_filename)
3. 通过 CDP `Runtime.evaluate` 在浏览器上下文执行 `fetch(url)`（helpers.py:418-435）
4. 用 `FileReader.readAsDataURL` 把 Blob 转为 base64
5. 回传 base64 后 decode 并写文件（helpers.py:444-457）

**这个方案有 5 个问题**：
- fetch 下载大文件时会占用页面主线程
- 跨域下载可能失败（CORS 限制）
- 需要手动管理 pending_download 状态机
- 无法获取下载进度
- 大文件下载会导致 CDP 通信超时

**Playwright 方案**只需一行：

```python
async with page.expect_download() as download_info:
    await page.get_by_text("Download").click()
download = await download_info.value
await download.save_as(str(download_dir / download.suggested_filename))
```

Playwright 的 download 事件：
- 自动处理浏览器下载流程
- 返回 `Download` 对象，包含文件流
- 支持进度回调
- 不受 CORS 限制（浏览器原生下载）
- 支持取消、获取文件大小

### 2.2 消除外部浏览器依赖（痛点 #2, #3）

当前代码中 `daemon.py:33-54` 维护了 **20 个**浏览器 profile 路径，`daemon.py:88-135` 维护了 **20+ 个**可执行文件路径（跨三平台 Chrome/Edge/Brave/Chromium 的各种变体）。

内嵌 Chromium 后：
- 删除整个 `find_chrome_executable()` 函数（~50 行）
- 删除整个 `_CHROME_PROFILES` 常量（~20 个路径）
- 删除 `_read_devtools_active_port()` 函数
- 删除 `get_ws_url()` 中的轮询逻辑
- 删除 `launch_browser()` 函数
- **总共消除 ~150 行脆弱的环境检测代码**

### 2.3 完全的用户隔离（痛点 #4）

当前问题：
- 用户浏览器与 Agent 共用同一个 GPU 进程
- 用户浏览器扩展可能干扰 CDP 命令
- 用户手动打开的对话框可能被 Agent 误处理
- Agent 创建的页面标签与用户标签混杂

内嵌 Chromium 后：Agent 使用完全独立的浏览器实例，**零干扰**。

### 2.4 版本可控

- Playwright 锁定 Chromium 版本（跟随 playwright 版本）
- 每次发布测试确定版本兼容性
- 不再出现"用户 Chrome 更新后 CDP API 变更"的问题

---

## 3. 内嵌 Chromium 的代价

### 3.1 二进制下载/缓存/更新机制

| 方案 | 下载机制 | 缓存位置 | 更新机制 |
|------|---------|---------|---------|
| Playwright | `playwright install chromium` → 自动下载到 `~/AppData/Local/ms-playwright/` | 系统级缓存 | 升级 playwright 版本后重新 install |
| `@puppeteer/browsers` | `npx @puppeteer/browsers install chrome@latest` | 指定目录 | 手动触发 |
| `chrome-launcher` | 不管理下载，仅启动 | N/A | N/A |

**Playwright 下载行为**：
- 首次运行 `playwright install` 下载 Chromium 压缩包
- Windows: `%USERPROFILE%\AppData\Local\ms-playwright\chromium-XXXX\chrome-win32\chrome.exe`
- macOS: `~/Library/Caches/ms-playwright/chromium-XXXX/chrome-mac/Chromium.app`
- Linux: `~/.cache/ms-playwright/chromium-XXXX/chrome-linux/chrome`
- 可通过环境变量 `PLAYWRIGHT_BROWSERS_PATH` 自定义位置

### 3.2 三平台支持

| 平台 | 支持情况 | 注意事项 |
|------|---------|---------|
| Windows (x64) | 全支持 | Playwright 提供 win64 构建 |
| macOS (x64) | 全支持 | mac10_15 / mac11 构建 |
| macOS (ARM64) | 全支持 | Apple Silicon 原生构建 |
| Linux (x64) | 全支持 | 需要系统依赖：libnss3, libnspr4, libatk-1.0-0 等 |
| Linux (ARM64) | Playwright 支持 | 测试较少 |

**Linux 系统依赖**：Playwright 在 Linux 上需要约 20 个系统包。可用 `playwright install-deps chromium` 自动安装，但需要在部署/CI 环境中运行。

### 3.3 ~150-200MB 体积

| 压缩包 | 解压后 | 影响 |
|--------|-------|------|
| ~90-110MB (zip/tgz) | ~150-200MB | 首次下载时间：~30-60s（100Mbps 网络） |

**缓存策略建议**：
- **开发环境**：`playwright install chromium` 一次，永久缓存
- **生产 Docker 镜像**：在构建阶段预下载到镜像中
- **CI/CD**：缓存 `~/.cache/ms-playwright/` 目录
- **离线部署**：预先下载并打包在安装包中

### 3.4 CI/CD 测试环境适配

```yaml
# GitHub Actions 示例
- name: Install Playwright Chromium
  run: |
    pip install playwright
    playwright install chromium  # ~60s
    playwright install-deps chromium  # Linux only, ~30s

# 缓存
- name: Cache Playwright browsers
  uses: actions/cache@v4
  with:
    path: ~/.cache/ms-playwright
    key: playwright-${{ runner.os }}-${{ hashFiles('pyproject.toml') }}
```

### 3.5 Google 许可证限制

- Chromium 是 BSD 许可的开源项目，**无商业使用限制**
- 内嵌 Chromium 二进制并分发需要遵守：
  - 保留 BSD 许可证声明
  - 不称其为"Google Chrome"
  - 不使用 Google Chrome 的商标/图标
- **注意**：通过 Playwright 安装的 Chromium 是 ungoogled-chromium 变体，移除 Google 服务的二进制文件
- Playwright 的分发许可已覆盖可再分发场景

---

## 4. 实现路径

### 4.1 轻量方案：playwright 仅管理 Chromium 二进制 + 启动

**核心思路**：只使用 Playwright 的 chromium 下载和 launch 能力，**不改变当前 daemon + IPC + helpers 架构**。

```
                   helpers.py (sync, IPC) → daemon.py (CDPClient)
                                                                ↑
                             仅在 manager.py / daemon.py 中替换浏览器启动：
                             playwright.chromium.launch() → 获取 CDP Endpoint
```

**改造范围**：

| 文件 | 改动量 | 改动内容 |
|------|--------|---------|
| `pyproject.toml` | +1 行 | 添加 `playwright>=1.48.0` 依赖 |
| `manager.py` | ~30 行 | BrowserManager.start() 中增加 `playwright install chromium` 自动下载检查 |
| `daemon.py` | ~150 行 | 替换 `find_chrome_executable()` + `launch_browser()` + `get_ws_url()` 为 `playwright.chromium.launch()`，但保持 CDPClient 和 IPC 不变 |
| `helpers.py` | ~50 行 | `accept_download()` 改为使用 Playwright 的 download 事件和 `Download.save_as()` |
| `daemon.py` (Daemon 类) | ~30 行 | 修改事件监听：用 Playwright 的 `page.on('download')` 替代 CDP `Browser.downloadWillBegin` |
| `README.md`（或 `.AIGEME/.skill/browser-control/SKILL.md`） | ~20 行 | 更新安装说明 |

**核心流程变化**：
```python
# daemon.py - 新启动方式 (轻量方案)
from playwright.sync_api import sync_playwright

class Daemon:
    async def start(self):
        # 不再需要 get_ws_url()
        # playwright 自动管理 Chromium 生命周期
        self._playwright = await asyncio.to_thread(self._launch_playwright)
        ws_endpoint = self._playwright.contexts[0].pages[0].context  # ... 获取 CDP endpoint
        # 后续 CDPClient 连接逻辑不变
        
    def _launch_playwright(self):
        """在线程中启动 playwright（playwright API 是同步的）"""
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=False,
            args=[
                '--no-first-run',
                '--no-default-browser-check',
            ],
        )
        return pw  # 需要保持引用以防止 GC
```

**下载处理变化**（这是关键改进）：
```python
# daemon.py - 使用 Playwright download 事件
class Daemon:
    async def start(self):
        # ... playwright 启动后 ...
        self._playwright_page.on("download", self._on_download)
    
    def _on_download(self, download):
        """Playwright 原生 download 事件"""
        self._pending_download = {
            "url": download.url,
            "suggested_filename": download.suggested_filename,
            "download_obj": download,  # Playwright Download 对象
        }
```

```python
# helpers.py - accept_download 简化
def accept_download(timeout=30.0):
    info = pending_download()
    if info and info.get("download_obj"):
        download = info["download_obj"]
        path = download.save_as(str(DOWNLOAD_DIR / info["suggested_filename"]))
        _send({"meta": "clear_download"})
        return {"status": "ok", "path": path, ...}
```

**优点**：
- 最小化改动：不修改架构，不改 helpers 接口
- 保留现有的所有 LLM tool 接口（tools.py 零改动）
- 保留 CLI 接口（cli.py 零改动）
- 保留 IPC 通信层
- 只替换浏览器启动和下载两个痛点

**缺点**：
- Playwright 引入了一个 ~30MB 的依赖包，但只用了 20% 的功能
- 需要在一个线程中同步运行 playwright（playwright 同步 API），与现有 asyncio 架构配合需 `asyncio.to_thread()`
- Playwright 管理的浏览器生命周期与现有 daemon 生命周期管理有重叠，需注意避免冲突

### 4.2 中等方案：`@puppeteer/browsers` 或 `chrome-launcher` 管理二进制

**核心思路**：只下载和管理 Chromium 二进制，不使用 Playwright API。保持现有 CDP 架构完全不变。

```
                    helpers.py (sync, IPC) → daemon.py (CDPClient)
                                                                 ↑
                         只在 manager.py 中增加二进制下载逻辑：
                         pip install @puppeteer/browsers → download chrome
                         或手动下载 → 缓存 → 启动
```

**改造范围**：

| 文件 | 改动量 | 改动内容 |
|------|--------|---------|
| `pyproject.toml` | +1 行 | 添加 `chrome-launcher>=1.1.0` |
| `daemon.py` | ~100 行 | 替换 `find_chrome_executable()` 为从固定缓存目录启动 Chromium |
| `manager.py` | ~30 行 | 首次运行时下载 Chromium 二进制 |
| `helpers.py` | ~0 行 | 零改动 |

**分析**：
- @puppeteer/browsers 是 Node.js 包，Python 项目不方便直接使用
- chrome-launcher 不管理下载，只管理启动
- **实际上没有很好的纯 Python Chromium 下载管理库**
- Python 生态中要管理 Chromium 下载，要么用 Playwright，要么自己实现下载逻辑

**自行实现的下载逻辑**（如果不用 Playwright）：

```python
# 自己管理 Chromium 下载
_CHROMIUM_URLS = {
    "win32": "https://storage.googleapis.com/chromium-browser-snapshots/Win/LAST_CHANGE",
    "darwin": "https://storage.googleapis.com/chromium-browser-snapshots/Mac/LAST_CHANGE",
    "linux": "https://storage.googleapis.com/chromium-browser-snapshots/Linux_x64/LAST_CHANGE",
}

def download_chromium(cache_dir: Path) -> Path:
    """自行下载 Chromium 二进制"""
    import urllib.request
    import zipfile
    
    platform_key = {"win32": "Win", "darwin": "Mac", "linux": "Linux_x64"}[sys.platform]
    base = f"https://storage.googleapis.com/chromium-browser-snapshots/{platform_key}"
    
    # 获取最新版本号
    rev = urllib.request.urlopen(f"{base}/LAST_CHANGE").read().decode().strip()
    cache_path = cache_dir / f"chromium-{rev}"
    if cache_path.exists():
        return cache_path / "chrome.exe"  # 或其他平台可执行文件
    
    # 下载并解压
    zip_url = f"{base}/{rev}/chrome-win32.zip"  # 或其他平台
    # ... ~150 行下载/解压/缓存逻辑
```

**评价**：自行管理 Chromium 下载相当于重造了一个 Playwright 的浏览器管理子系统，工作量 > 轻量方案但收益更低。**不推荐**。

### 4.3 重度方案：完全拥抱 Playwright

**核心思路**：用 Playwright API 完全替换现有的 daemon + IPC + helpers 三层架构。

```
                    tools.py (async) → manager.py → Playwright API
                                                           │
                                              playwright.chromium.launch()
                                              page.goto(), page.click(), ...
                                              page.on('download', ...)
```

**改造范围**：

| 文件 | 改动量 | 改动内容 |
|------|--------|---------|
| `pyproject.toml` | +1 行 | `playwright>=1.48.0` |
| `daemon.py` | **完全重写** (~200 行代替 557 行) | 用 Playwright 同步 API + asyncio 封装代替 CDPClient |
| `manager.py` | **完全重写** (~50 行代替 148 行) | 简化为 Playwright 生命周期管理 |
| `helpers.py` | **完全重写** (~200 行代替 687 行) | 所有操作映射到 Playwright API |
| `_ipc.py` | **删除** (~236 行) | 不再需要 IPC 层 |
| `cli.py` | 修改 (~100 行) | 适配新的 helpers 接口 |
| `tools.py` | 少量修改 (~50 行) | 适配 Manager 接口变化 |
| `__init__.py` | 修改 | 移除 `_ipc` 引用 |

**Playwright API 映射**（helpers.py 完全重写）：

```python
# 新 helpers.py 示例
from playwright.sync_api import sync_playwright, Page, Browser, Download

_pw = None
_browser: Browser = None
_page: Page = None

def ensure_browser():
    global _pw, _browser, _page
    if _page and _page.is_visible():
        return
    if _pw is None:
        _pw = sync_playwright().start()
    if _browser is None or not _browser.is_connected():
        _browser = _pw.chromium.launch(headless=False)
    if _page is None:
        _page = _browser.new_page()

def goto_url(url: str):
    ensure_browser()
    _page.goto(url, wait_until="networkidle")

def click_at_xy(x: int, y: int, button: str = "left"):
    ensure_browser()
    _page.mouse.click(x, y, button=button)

def capture_screenshot(path=None, full=False):
    ensure_browser()
    if full:
        _page.screenshot(path=path, full_page=True)
    else:
        _page.screenshot(path=path)

def accept_download(timeout=30.0):
    ensure_browser()
    with _page.expect_download(timeout=timeout * 1000) as download_info:
        # 等待下载触发
        pass  
    download = download_info.value
    path = download.save_as(str(DOWNLOAD_DIR / download.suggested_filename))
    return {"status": "ok", "path": path, ...}
```

**优点**：
- 功能最强大：Playwright 提供了 auto-wait、locator、多页面管理、iframe 处理、网络拦截等高级能力
- 下载处理：Playwright 原生 download 事件，零 CDP hack
- 代码量大幅减少：~400 行代替 ~1500 行（daemon + helpers + _ipc）
- 更稳定：Playwright 封装了 CDP 的底层细节和版本兼容
- 测试友好：Playwright 本身就是测试框架，易于编写集成测试

**缺点**：
- 学习成本高：团队需要熟悉 Playwright API
- 重写工作量大：整个 browser 模块需要重写
- 现有 CLI 接口需要适配：cli.py 依赖 helpers 的函数签名
- LLM tool 接口需要适配：tools.py 中 exec(code) 的全局变量空间
- 丧失底层 CDP 控制权：某些高级 CDP 命令可能无法通过 Playwright 表达（但可通过 `page.evaluate()` + CDP session 弥补）

---

## 5. 推荐方案

### **推荐：轻量方案（playwright 管理二进制 + 启动，不改架构）**

**理由**：

1. **最小改动，最大收益**：只替换浏览器启动和下载两个痛点，不改变现有架构。所有 LLM tool 接口、CLI 接口、IPC 通信保持不变。

2. **下载问题得到彻底解决**：用 Playwright 的 `page.on('download')` 替换当前复杂的 CDP 事件监听 + fetch 回传方案。这是当前架构最大的痛点，轻量方案正好解决。

3. **删除 150+ 行脆弱代码**：`find_chrome_executable()`, `_CHROME_PROFILES`, `launch_browser()`, `_read_devtools_active_port()`, `get_ws_url()` 中大部分逻辑可以被删除。

4. **渐进式迁移**：轻量方案是整个改造的第一步。如果后续完全拥抱 Playwright 的价值更大（如 multi-page、auto-wait、iframe 处理等），可以在轻量方案基础上逐步演进到重度方案。

5. **不影响现有接口契约**：
   - `tools.py` 的 `browser_execute`/`browser_search`/`browser_extract` 三个工具接口零改动
   - `cli.py` 的所有子命令和帮助文本零改动
   - `helpers.py` 中 ~30 个 helper 函数的签名零改动（内部实现可改）

6. **Playwright 的下载管理是 Python 生态中 Chromium 二进制管理的最佳实践**——没有比它更好的选择。`@puppeteer/browsers` 是 Node.js 生态的工具，Python 中自行管理下载相当于重新实现 Playwright 的浏览器管理功能。

### 不推荐中等方案的理由

- Python 生态中缺乏成熟的 Chromium 二进制管理库
- 自行实现下载逻辑相当于重新发明 Playwright 的浏览器管理功能
- 工作量 > 轻量方案，但收益更低

### 不推荐重度方案（现阶段）的理由

- 改造范围太大，风险高
- 当前的 daemon + IPC + helpers 架构虽然复杂，但功能完整且经过测试
- 可以先走轻量方案解决痛点，后续根据需求演进

---

## 6. 迁移路线图

### 阶段一：引入 Playwright 依赖 + 二进制管理（1-2 天）

**改动文件**：

| 文件 | 改动 | 行数 |
|------|------|------|
| `pyproject.toml` | 添加 `playwright>=1.48.0` | +1 |
| `manager.py` | 在 `BrowserManager.start()` 中增加 `playwright install chromium` 检查和自动安装 | ~30 行 |

**核心逻辑**：

```python
# manager.py 新增
import subprocess
import shutil

def _ensure_chromium_installed():
    """确保 Playwright Chromium 已下载"""
    # 检查 playwright 的 Chromium 缓存路径
    playwright_chromium_path = Path.home() / "AppData" / "Local" / "ms-playwright"
    if not any(playwright_chromium_path.glob("chromium-*")):
        logger.info("Chromium not found, downloading via playwright install...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
```

**交付物**：`python -m core.tools.browser.cli page_info` 可以使用 Playwright 下载的 Chromium。

### 阶段二：替换 daemon.py 中的浏览器查找/启动逻辑（2-3 天）

**改动文件**：

| 文件 | 改动 | 行数 |
|------|------|------|
| `daemon.py` | 删除 `find_chrome_executable()`, `_CHROME_PROFILES`, `_read_devtools_active_port()`, `launch_browser()` | -150 |
| `daemon.py` | 新增 `_launch_playwright_chromium()` 方法 | +50 |
| `daemon.py` | 修改 `get_ws_url()` 为从 Playwright 获取 CDP Endpoint | +20 |
| `daemon.py` | `Daemon.start()` 中调用新启动逻辑 | ~10 |

**关键变化**：

```python
class Daemon:
    _playwright_instance = None  # 防止 GC
    
    async def start(self):
        # 使用 playwright 启动内嵌 Chromium，获取 WS URL
        url = await asyncio.to_thread(self._launch_internal_chromium)
        # 后续 CDPClient 连接逻辑完全不变
        self.cdp = CDPClient(url)
        await self.cdp.start()
        ...
    
    def _launch_internal_chromium(self) -> str:
        """同步函数：启动内嵌 Chromium 并返回 CDP WebSocket URL"""
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=False,
            args=[
                '--no-first-run',
                '--no-default-browser-check',
                '--no-startup-window',
            ],
        )
        # 获取 CDP URL 供 CDPClient 使用
        # Playwright 可以通过 CDPSession 暴露底层 CDP
        cdp_url = browser.contexts[0].pages[0].context  # 实际需通过 CDPSession
        
        self._playwright_instance = pw  # 保持引用
        return cdp_url
```

**交付物**：daemon 使用内嵌 Chromium 启动，所有已有 CDP 命令正常工作。

### 阶段三：优化下载处理（2-3 天）

**改动文件**：

| 文件 | 改动 | 行数 |
|------|------|------|
| `daemon.py` | 用 Playwright 的 `page.on('download')` 替换 `Browser.downloadWillBegin` 事件监听 | ~30 |
| `helpers.py` | 简化 `accept_download()` 使用 Playwright 的 `Download.save_as()` | ~50 |
| `helpers.py` | 删除 `accept_download()` 中复杂的 CDP Runtime `fetch()` 回传逻辑 | ~40 |

**关键变化**：

```python
# daemon.py - 新 download 处理
class Daemon:
    def _on_download(self, download):
        """Playwright download 事件回调（在线程中）"""
        self._pending_download = {
            "url": download.url,
            "suggested_filename": download.suggested_filename,
            "download": download,  # Playwright Download 对象
        }
```

```python
# helpers.py - 简化的 accept_download
def accept_download(timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = _send({"meta": "pending_download"})
        info = resp.get("download")
        if info and "download" in info:
            download = info["download"]
            filename = info["suggested_filename"]
            path = str(DOWNLOAD_DIR / filename)
            download.save_as(path)  # Playwright 处理文件保存
            _send({"meta": "clear_download"})
            return {"status": "ok", "path": path, ...}
        time.sleep(0.3)
    return {"error": f"下载超时 ({timeout}s)"}
```

**交付物**：下载功能通过 Playwright 原生支持工作，不再依赖 CDP fetch hack。

### 阶段四（可选）：逐步演进到重度方案

如果未来需要更强大的浏览器控制能力，可以在阶段三基础上平滑演进：

1. 在 `helpers.py` 中新增基于 Playwright locator 的智能操作函数（如 `click_text()`, `fill_by_placeholder()`）
2. 逐步将 CDP 命令迁移到 Playwright API
3. 最终删除 `_ipc.py` 和 `daemon.py` 中的 IPC 层，让 helpers 直接调用 Playwright
4. 删除 CDPClient 依赖（`cdp_use` 包）

---

## 7. 文件改动总览

| 阶段 | 文件 | 操作 | 说明 |
|------|------|------|------|
| 一 | `pyproject.toml` | 编辑 | 添加 `playwright>=1.48.0` 依赖 |
| 一 | `manager.py` | 编辑 | 添加 Chromium 自动下载检查 |
| 二 | `daemon.py` | **大改** | 替换查找/启动为 Playwright launch，保留 CDPClient + IPC |
| 二 | `daemon.py` | 删除 | `find_chrome_executable()`, `_CHROME_PROFILES`, `_read_devtools_active_port()`, `launch_browser()`, 简化 `get_ws_url()` |
| 三 | `daemon.py` | 编辑 | `Daemon` 类增加 Playwright download 事件处理 |
| 三 | `helpers.py` | 编辑 | 简化 `accept_download()` 和 `pending_download()` |
| — | `cli.py` | **零改动** | CLI 接口不变 |
| — | `tools.py` | **零改动** | LLM tool 接口不变 |
| — | `_ipc.py` | **零改动** | IPC 通信层不变 |
| — | `helpers.py` (除下载外) | **零改动** | 其他 helper 函数签名不变 |

---

## 8. Playwright 与当前架构的集成要点

### 8.1 asyncio + 同步 Playwright API

Playwright 有同步和异步两套 API。当前 `daemon.py` 使用 asyncio，而 Playwright 的 chromium.launch() 是同步操作。

**解决方案**：使用 `asyncio.to_thread()` 将 Playwright 同步调用放到线程池执行。

```python
class Daemon:
    async def start(self):
        # 在独立线程中启动 Playwright
        url = await asyncio.to_thread(self._launch_internal_chromium)
        # 继续使用 CDPClient（asyncio）
        self.cdp = CDPClient(url)
        await self.cdp.start()
```

或者使用 Playwright 的异步 API：

```python
from playwright.async_api import async_playwright

class Daemon:
    async def start(self):
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=False)
        # 通过 CDPSession 获取底层 CDP 连接
        page = await browser.new_page()
        cdp_session = await page.context.new_cdp_session(page)
        ws_url = cdp_session._ws_endpoint  # 或其他方式获取
        self.cdp = CDPClient(ws_url)
        await self.cdp.start()
```

### 8.2 Playwright 生命周期管理

Playwright 的 browser 对象需要在 daemon 进程中保持引用，防止被 GC 回收断开连接。Daemon.shutdown() 中需要调用 `browser.close()` 和 `playwright.stop()`。

### 8.3 下载事件跨线程

Playwright 的 download 事件在 Playwright 内部线程触发，需要通过线程安全的方式传递到 daemon 的 `_pending_download` 属性。使用 `threading.Lock` 或 `queue.Queue` 保护。

---

## 9. 风险评估

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| Playwright 下载 Chromium 失败（网络问题） | 高 | 中 | 添加重试机制、提供离线安装文档、支持环境变量指定自定义下载镜像 |
| Chromium 版本与 CDP API 不兼容 | 中 | 低 | Playwright 锁定的 Chromium 版本与 Playwright 版本绑定，升级时需测试 |
| Playwright 内部线程与 asyncio 事件循环冲突 | 高 | 低 | 使用 `asyncio.to_thread()` 隔离，关键路径加锁保护 |
| Linux CI 环境缺少系统依赖 | 中 | 中 | 使用 `playwright install-deps` 自动安装，Docker 镜像预装 |
| 150-200MB 下载影响首次使用体验 | 中 | 高 | 后台静默下载，首次启动时显示进度；Docker 镜像预缓存 |
| Playwright 许可证变更 | 低 | 低 | Apache 2.0 许可，商业友好 |

---

## 10. 总结

| 项目 | 结论 |
|------|------|
| **推荐方案** | 轻量方案：Playwright 管理 Chromium 二进制 + 启动，不改架构 |
| **总改动文件** | 4 个文件（`pyproject.toml`, `manager.py`, `daemon.py`, `helpers.py`） |
| **零改动文件** | `cli.py`（385 行）、`tools.py`（310 行）、`_ipc.py`（236 行）、`__init__.py` |
| **删除代码** | ~150 行脆弱的浏览器环境检测代码（`find_chrome_executable`, `_CHROME_PROFILES`, `_read_devtools_active_port`, `launch_browser` 等） |
| **新增代码** | ~100 行 Playwright 集成代码 |
| **解决痛点** | 下载（#1）、外部依赖（#2, #3）、用户隔离（#4）全部解决 |
| **迁移周期** | 3 个阶段，共 5-8 个工作日 |
| **备选** | 阶段四（演进到重度方案）按需执行，非必须 |
