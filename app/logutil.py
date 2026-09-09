from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import settings

_configured = False


def get_logger() -> logging.Logger:
    global _configured
    log = logging.getLogger("local_agent")
    if _configured:
        return log
    log.setLevel(logging.INFO)
    log.propagate = False
    path = settings.log_path
    handler = RotatingFileHandler(
        path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    _configured = True
    return log
