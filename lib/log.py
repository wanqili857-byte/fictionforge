"""
log.py — 共享日志配置。

用法:
    from log import get_logger
    log = get_logger(__name__)
    log.info("msg")
    log.warning("warn")
    log.error("err")
"""

import logging
import sys


_LOGGERS: dict[str, logging.Logger] = {}


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """获取或创建 logger。同一 name 返回同一实例。"""
    if name in _LOGGERS:
        return _LOGGERS[name]

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    _LOGGERS[name] = logger
    return logger


def set_level(level: int):
    """全局调整日志等级。"""
    logging.getLogger().setLevel(level)
    for l in _LOGGERS.values():
        l.setLevel(level)
