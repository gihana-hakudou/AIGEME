"""统一诊断日志模块

替换 main.py 与 ws_server.py 中各自独立的 _diag() 函数：
- 统一用 Python logging + RotatingFileHandler，避免并发写入竞态
- 按模块名区分日志来源（main / ws_server）
- 内部异常不会被静默吞掉（logging 自带异常处理）

用法：
    from core.engine.diag_logger import diag
    diag("some message")                    # 来源: diag
    diag("some message", source="main")     # 来源: main
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DIAG_LOG = Path(__file__).resolve().parent.parent.parent / "diag_ws.log"

_logger = logging.getLogger("diag")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False  # 不传播到根 logger，避免重复输出

if not _logger.handlers:
    _handler = RotatingFileHandler(
        filename=str(_DIAG_LOG),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=2,
        encoding="utf-8",
    )
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(source)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    _logger.addHandler(_handler)


def diag(msg: str, source: str = "diag") -> None:
    """写诊断日志到文件（线程安全，自带轮转）

    Args:
        msg: 日志消息
        source: 日志来源标识（main / ws_server / diag）
    """
    _logger.debug(msg, extra={"source": source})
