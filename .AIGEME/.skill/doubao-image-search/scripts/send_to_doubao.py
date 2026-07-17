"""
豆包以图搜图 - 完整流程脚本
通过系统级模拟按键操作豆包页面，绕过浏览器沙箱限制

用法:
    python send_to_doubao.py <图片路径> [提示文字]

示例:
    python send_to_doubao.py C:/temp/upload_img.jpg "帮我识别这张图是什么漫画"
"""

import sys
import win32api
import win32con
import win32gui
import win32clipboard
import time
from io import BytesIO
from PIL import Image
import os


def set_clipboard_image(img_path):
    """将图片文件写入系统剪贴板"""
    if not os.path.exists(img_path):
        print(f"文件不存在: {img_path}")
        return False
    
    img = Image.open(img_path)
    output = BytesIO()
    img.convert("RGB").save(output, format="BMP")
    data = output.getvalue()[14:]
    output.close()
    
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    win32clipboard.CloseClipboard()
    return True


def set_clipboard_text(text):
    """将文字写入系统剪贴板"""
    win32clipboard.OpenClipboard()
    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    win32clipboard.CloseClipboard()


def find_doubao_window():
    """查找豆包浏览器窗口"""
    windows = []
    
    def callback(hwnd, result):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if '\u8c46\u5305' in title or 'Doubao' in title:
                result.append((hwnd, title))
    
    win32gui.EnumWindows(callback, windows)
    return windows


def send_hotkey(hwnd, key1, key2=None):
    """向指定窗口发送快捷键"""
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    
    if key2:
        win32api.keybd_event(0x11, 0, 0, 0)
        win32api.keybd_event(key2, 0, 0, 0)
        time.sleep(0.1)
        win32api.keybd_event(key2, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(0x11, 0, win32con.KEYEVENTF_KEYUP, 0)
    else:
        win32api.keybd_event(key1, 0, 0, 0)
        time.sleep(0.1)
        win32api.keybd_event(key1, 0, win32con.KEYEVENTF_KEYUP, 0)


def main():
    if len(sys.argv) < 2:
        print("\u7528\u6cd5: python send_to_doubao.py <\u56fe\u7247\u8def\u5f84> [\u63d0\u793a\u6587\u5b57]")
        print("\u793a\u4f8b: python send_to_doubao.py C:/temp/upload_img.jpg \"\u5e2e\u6211\u8bc6\u522b\u8fd9\u5f20\u56fe\u662f\u4ec0\u4e48\u6f2b\u753b\"")
        sys.exit(1)
    
    img_path = sys.argv[1]
    prompt = sys.argv[2] if len(sys.argv) > 2 else "\u5e2e\u6211\u8bc6\u522b\u8fd9\u5f20\u56fe\u662f\u4ec0\u4e48\u6f2b\u753b\u3001\u89d2\u8272\u548c\u51fa\u5904"
    
    print(f"[1/4] \u8bbe\u7f6e\u526a\u8d34\u677f\u56fe\u7247: {img_path}")
    if not set_clipboard_image(img_path):
        sys.exit(1)
    
    print("[2/4] \u67e5\u627e\u8c46\u5305\u7a97\u53e3...")
    windows = find_doubao_window()
    if not windows:
        print("\u274c \u672a\u627e\u5230\u8c46\u5305\u7a97\u53e3\uff0c\u8bf7\u5148\u6253\u5f00\u8c46\u5305\u9875\u9762")
        sys.exit(1)
    
    hwnd = windows[0][0]
    title = windows[0][1]
    print(f"   \u627e\u5230\u7a97\u53e3: {title}")
    
    print("[3/4] \u7c98\u8d34\u56fe\u7247...")
    send_hotkey(hwnd, None, 0x56)
    time.sleep(2)
    
    print("[4/4] \u8f93\u5165\u63d0\u793a\u6587\u5b57\u5e76\u53d1\u9001...")
    set_clipboard_text(prompt)
    time.sleep(0.3)
    send_hotkey(hwnd, None, 0x56)
    time.sleep(0.5)
    
    send_hotkey(hwnd, 0x0D)
    print("\u2705 \u5df2\u53d1\u9001\uff0c\u7b49\u5f85\u8c46\u5305\u56de\u590d...")


if __name__ == "__main__":
    main()
