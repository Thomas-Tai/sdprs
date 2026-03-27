"""
結構化日誌模組

設定 root logger 同時輸出到檔案和 stderr。

使用範例：
    from utils.logger import setup

    setup()  # 初始化日誌設定

    import logging
    logger = logging.getLogger(__name__)
    logger.info("Application started")
"""

import logging
import logging.handlers
import os
import sys
import tempfile

_is_setup = False


def setup(level: int = logging.INFO, log_file: str = None) -> None:
    """
    初始化全域日誌設定。

    設定 root logger 同時輸出到：
    1. RotatingFileHandler（預設 /tmp/sdprs-edge.log, 5MB, 3 備份）
    2. StreamHandler（stderr）

    Args:
        level: 日誌等級（預設 INFO）
        log_file: 日誌檔案路徑（預設 /tmp/sdprs-edge.log，Windows 下使用 temp 目錄）
    """
    global _is_setup

    if _is_setup:
        # 避免重複設定
        return

    # 確定日誌檔案路徑
    if log_file is None:
        if sys.platform == "win32":
            # Windows 下使用 temp 目錄
            log_dir = tempfile.gettempdir()
            log_file = os.path.join(log_dir, "sdprs-edge.log")
        else:
            log_file = "/tmp/sdprs-edge.log"

    # 確保日誌目錄存在
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 日誌格式
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 取得 root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除現有的 handlers（避免重複）
    root_logger.handlers.clear()

    # 1. RotatingFileHandler
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5_242_880,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 2. StreamHandler (stderr)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    _is_setup = True

    logging.getLogger(__name__).info(f"Logging initialized: file={log_file}, level={logging.getLevelName(level)}")


if __name__ == "__main__":
    # 測試日誌設定
    setup(level=logging.DEBUG)

    logger = logging.getLogger("test_module")

    logger.debug("This is a debug message")
    logger.info("This is an info message")
    logger.warning("This is a warning message")
    logger.error("This is an error message")

    # 測試多次呼叫 setup 不會重複
    setup()
    setup()

    logger.info("Setup called multiple times - should only initialize once")