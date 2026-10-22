"""Application logging setup."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path, level_name: str = "INFO") -> logging.Logger:
    """Configure a UTF-8 rotating file logger once for the application."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("network_monitor")
    logger.setLevel(getattr(logging, level_name.upper(), logging.INFO))
    logger.propagate = False
    if logger.handlers:
        return logger

    handler = RotatingFileHandler(
        log_dir / "monitor.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    return logger
