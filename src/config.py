"""
config.py
=========
Configuracion del worker, entera desde el entorno.

Sin fallbacks: si falta una variable, el worker NO arranca y dice cual falta.
Es a proposito -- un worker que arranca "a medias" apuntando a ningun lado se
pasa el dia logueando 404 y nadie se entera de por que.

En la VPS estas variables se cargan en el panel de Dokploy (Environment), no
en un .env commiteado.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Solo conveniencia local. En Dokploy las variables vienen inyectadas.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv es opcional
    pass


class ConfigError(RuntimeError):
    """Falta una variable obligatoria."""


# --- Algoritmo ------------------------------------------------------------- #
NORMAL_STRENGTH = 2.0
NORMAL_FORMAT = "png"

# --- Ciclo del worker ------------------------------------------------------ #
MAX_ATTEMPTS = 3

# El disparador real es SSE (una normal lista en ~2s). Este poll es la RED DE
# SEGURIDAD: agarra lo que se subio mientras el worker estaba desconectado, que
# el realtime no reenvia.
POLL_INTERVAL = 300

# Un record en `processing` mas viejo que esto se da por abandonado y vuelve a
# `pending`. Cubre el caso "el contenedor murio a mitad de un job": sin esto la
# textura queda trabada en processing para siempre y nadie la reclama.
STALE_CLAIM_SECONDS = 600

_REQUIRED = (
    "PB_URL",
    "PB_WORKER_EMAIL",
    "PB_WORKER_PASSWORD",
    "PB_USERS",
    "PB_TEXTURES",
)


@dataclass(frozen=True)
class Config:
    pb_url: str
    worker_email: str
    worker_password: str
    col_users: str
    col_textures: str

    @staticmethod
    def load() -> "Config":
        missing = [n for n in _REQUIRED if not (os.environ.get(n) or "").strip()]
        if missing:
            raise ConfigError(
                "Faltan variables obligatorias (sin fallback): "
                + ", ".join(missing)
                + ". En local: copia .env.example a .env y rellenalas. "
                "En la VPS: cargalas en Environment, en el panel de Dokploy."
            )
        return Config(
            pb_url=os.environ["PB_URL"].strip().rstrip("/"),
            worker_email=os.environ["PB_WORKER_EMAIL"].strip(),
            worker_password=os.environ["PB_WORKER_PASSWORD"].strip(),
            col_users=os.environ["PB_USERS"].strip(),
            col_textures=os.environ["PB_TEXTURES"].strip(),
        )


_cfg: Config | None = None


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config.load()
    return _cfg
