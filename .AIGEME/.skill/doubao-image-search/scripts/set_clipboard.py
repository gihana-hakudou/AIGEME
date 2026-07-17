import win32clipboard
from io import BytesIO
from PIL import Image
import os

img_path = r'C:\temp\upload_img1.jpg'
if not os.path.exists(img_path):
    print(f"File not found: {img_path}")
    exit(1)

img = Image.open(img_path)
output = BytesIO()
img.convert("RGB").save(output, format="BMP")
data = output.getvalue()[14:]  # 去掉BMP文件头
output.close()

win32clipboard.OpenClipboard()
win32clipboard.EmptyClipboard()
win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
win32clipboard.CloseClipboard()
print("Clipboard set with image successfully")
