"""
logs.py
=======
Logging a stdout, y nada mas.

Antes escribia tambien a logs/dnormal.log porque el dashboard leia ese archivo
para mostrar el panel en vivo. Sin dashboard, ese archivo no lo lee nadie: lo
unico que lograba era obligar al contenedor a tener un volumen para no perder
logs que igual se ven mejor en el panel de Dokploy.
"""

from __future__ import annotations

import logging
import sys

_configured = False


def get_logger(name: str = "normal-worker") -> logging.Logger:
    global _configured
    logger = logging.getLogger(name)
    if not _configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                              "%Y-%m-%d %H:%M:%S")
        )
        root = logging.getLogger("normal-worker")
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        root.propagate = False
        _configured = True
    return logger
