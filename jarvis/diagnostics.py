from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading


def configure_logging(root: Path) -> logging.Logger:
    directory = root / "logs"
    directory.mkdir(exist_ok=True)
    logger = logging.getLogger("jarvis")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(directory / "jarvis.log", maxBytes=2 * 1024 * 1024,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
    return logger


def install_thread_error_handler(logger: logging.Logger) -> None:
    def report(args: threading.ExceptHookArgs) -> None:
        logger.critical("Ошибка фонового потока %s", args.thread.name if args.thread else "?",
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = report
