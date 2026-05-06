"""Structured JSON logging for the backend."""
from __future__ import annotations

import logging
import sys

try:
    from pythonjsonlogger.json import JsonFormatter as _JsonFormatter
except ImportError:
    from pythonjsonlogger.jsonlogger import JsonFormatter as _JsonFormatter


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    formatter = _JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for noisy in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).handlers = [handler]
