"""
本地 OCR 工具 — 基于 PaddleOCR

用法:
    python scripts/ocr.py <图片路径>
    python scripts/ocr.py <图片路径> --lang ch
    python scripts/ocr.py <图片路径> --lang en --detail

参数:
    path        图片路径（必填）
    --lang      语言：ch/en/ch_en（默认 ch）
    --detail    输出详细结果（位置、置信度）
"""

import argparse
import sys
from pathlib import Path

# 屏蔽 PaddleOCR 启动时的非必要日志
import logging
logging.getLogger().setLevel(logging.ERROR)

from paddleocr import PaddleOCR


def ocr(image_path: str, lang: str = "ch", detail: bool = False) -> list[dict]:
    """
    识别图片中的文字。

    Args:
        image_path: 图片文件路径
        lang: 识别语言（ch/en/ch_en）
        detail: 是否返回详细信息

    Returns:
        识别到的文字列表，每项包含 text 和（可选）confidence、box
    """
    lang_map = {
        "ch": "ch",
        "en": "en",
        "ch_en": "ch",
    }
    # ch 模式下 PaddleOCR 默认中英文都识别
    ocr_engine = PaddleOCR(use_textline_orientation=True, lang=lang_map.get(lang, "ch"))

    result = ocr_engine.ocr(image_path, cls=True)

    output = []
    if result and result[0]:
        for line in result[0]:
            box = line[0]
            text, confidence = line[1]
            entry = {"text": text}
            if detail:
                entry["confidence"] = round(confidence, 4)
                entry["box"] = [[round(c, 2) for c in point] for point in box]
            output.append(entry)

    return output


def main():
    parser = argparse.ArgumentParser(description="本地 OCR 识别工具")
    parser.add_argument("path", help="图片路径")
    parser.add_argument("--lang", default="ch", choices=["ch", "en", "ch_en"],
                        help="识别语言（默认 ch，自动识别中英文）")
    parser.add_argument("--detail", action="store_true",
                        help="输出详细结果（位置、置信度）")
    args = parser.parse_args()

    img_path = Path(args.path)
    if not img_path.exists():
        print(f"错误: 文件不存在 — {img_path}")
        sys.exit(1)

    supported = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp")
    if img_path.suffix.lower() not in supported:
        print(f"错误: 不支持的图片格式（支持: {', '.join(supported)}）")
        sys.exit(1)

    results = ocr(str(img_path), args.lang, args.detail)

    if not results:
        print("未识别到文字")
        return

    if args.detail:
        for item in results:
            conf = item.get("confidence", 0)
            print(f"[{conf:.1%}] {item['text']}")
    else:
        for item in results:
            print(item["text"])


if __name__ == "__main__":
    main()
