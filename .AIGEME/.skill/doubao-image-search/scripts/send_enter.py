import win32api
import win32con
import win32gui
import time

def enum_windows_callback(hwnd, windows):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        if '豆包' in title or 'Doubao' in title:
            windows.append((hwnd, title))

windows = []
win32gui.EnumWindows(enum_windows_callback, windows)

for hwnd, title in windows:
    print(f"Found: {title}")
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    win32api.keybd_event(0x0D, 0, 0, 0)
    time.sleep(0.1)
    win32api.keybd_event(0x0D, 0, win32con.KEYEVENTF_KEYUP, 0)
    print("Enter sent")
    break
