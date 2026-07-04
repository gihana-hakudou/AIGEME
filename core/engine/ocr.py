"""OCR 文字识别引擎 — 多模态降级兜底

惰性加载 RapidOCR（首次调用时下载模型），
在 run_in_executor 中执行避免阻塞事件循环。

用法:
    from core.engine.ocr import ocr_image
    text = await ocr_image("path/to/image.jpg")
    if text:
        print(text)
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# 全局缓存 OCR 引擎实例（惰性加载）
_ocr_engine: object | None = None
_ocr_available: bool | None = None  # None=未尝试, True=可用, False=彻底不可用


def _get_engine():
    """获取 OCR 引擎实例（惰性加载）"""
    global _ocr_engine, _ocr_available

    if _ocr_available is False:
        return None

    if _ocr_engine is not None:
        return _ocr_engine

    try:
        from rapidocr_onnxruntime import RapidOCR

        import time
        t0 = time.time()
        _ocr_engine = RapidOCR()
        logger.info("RapidOCR 引擎加载成功 (%.0fms)", (time.time() - t0) * 1000)
        _ocr_available = True
        return _ocr_engine
    except ImportError:
        logger.warning("rapidocr-onnxruntime 未安装，OCR 不可用")
        _ocr_available = False
        return None
    except Exception as e:
        logger.warning("RapidOCR 引擎加载失败: %s", e)
        _ocr_engine = None  # 允许下次重试（可能是权限等临时问题）
        return None


async def ocr_image(image_path: str) -> str | None:
    """对图片文件进行 OCR 识别，返回提取的文本。

    纯本地计算，无网络依赖。在 run_in_executor 中执行以避免阻塞事件循环。

    Args:
        image_path: 图片文件路径

    Returns:
        识别到的文本（按阅读顺序拼接，换行分隔），失败返回 None
    """
    engine = _get_engine()
    if engine is None:
        return None

    if not image_path:
        return None

    try:
        import time
        t0 = time.time()

        # OCR 是 CPU 密集型，在 run_in_executor 中执行
        result, elapse = await asyncio.get_event_loop().run_in_executor(
            None, engine, image_path
        )

        elapsed = (time.time() - t0) * 1000
        logger.debug("OCR 完成: %.0fms, %d 文本区域", elapsed, len(result) if result else 0)

        if not result:
            logger.info("OCR 未检测到文字: %s", image_path)
            return None

        # 按 y 坐标排序（从上到下保持阅读顺序）
        result_sorted = sorted(result, key=lambda x: x[0][0][1])

        texts = []
        for bbox, text, conf in result_sorted:
            confidence = float(conf)
            if confidence >= 0.3:  # 过滤过低置信度的噪点
                texts.append(text)

        if not texts:
            return None

        return "\n".join(texts)

    except FileNotFoundError:
        logger.warning("OCR 图片文件不存在: %s", image_path)
        return None
    except Exception as e:
        logger.warning("OCR 识别失败: %s", e)
        return None
