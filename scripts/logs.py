"""
logs.py
=======
Logging compartido. Escribe a stdout (lo captura docker) y a logs/donormal.log,
que el dashboard lee para mostrar el panel de logs en vivo.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.environ.get("LOG_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
)
LOG_FILE = os.path.join(_LOG_DIR, "donormal.log")

_configured = False


def get_logger(name: str = "donormal") -> logging.Logger:
    global _configured
    logger = logging.getLogger(name)
    if _configured:
        return logger

    os.makedirs(_LOG_DIR, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)

    fileh = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fileh.setFormatter(fmt)

    root = logging.getLogger("donormal")
    root.setLevel(logging.INFO)
    root.addHandler(stream)
    root.addHandler(fileh)
    root.propagate = False

    _configured = True
    return logger


def tail_log(lines: int = 200) -> str:
    """Devuelve las últimas N líneas del log (para el dashboard)."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except FileNotFoundError:
        return ""
