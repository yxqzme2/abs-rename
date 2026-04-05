"""
app/utils/logging.py
--------------------
Configures the root logger for the application.
Call setup_logging() once at startup (from main.py).
All other modules use: import logging; logger = logging.getLogger(__name__)
"""

import logging
import sys
from app.config import DEBUG


def setup_logging() -> None:
    level = logging.DEBUG if DEBUG else logging.INFO

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers if called more than once
    if not root.handlers:
        root.addHandler(handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
