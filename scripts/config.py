"""
config.py
=========
Carga la configuración desde variables de entorno (.env).

Sin fallbacks: si falta PB_URL, PB_TOKEN, PB_USERS o PB_DATA, el sistema
levanta un error y NO arranca. Es a propósito — así nadie corre el sistema
"a medias" apuntando a ningún lado.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Carga .env si python-dotenv está disponible (en local). En docker las vars
# vienen inyectadas por compose, así que esto es solo conveniencia.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv es opcional
    pass


class ConfigError(RuntimeError):
    """Falta una variable obligatoria o tiene un valor inválido."""


_REQUIRED = ("PB_URL", "PB_TOKEN", "PB_USERS", "PB_DATA")

# --- Valores hardcodeados (NO van en .env) ---
NORMAL_STRENGTH = 2.0          # fuerza del normal map (default del Normal_creator)
NORMAL_FORMAT = "png"          # png | exr
WEB_PORT = 8753                # http://localhost:8753
WORKER_ENABLED = False         # FASE 2: worker automático apagado
WORKER_SCAN_INTERVAL = 300     # seg entre scans del worker (fase 2)


def _require(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise ConfigError(
            f"Falta la variable de entorno obligatoria: {name}. "
            f"Copiá .env.example a .env y rellenala (sin fallbacks)."
        )
    return value


@dataclass(frozen=True)
class Config:
    pb_url: str
    pb_token: str
    pb_users: str
    pb_data: str
    normal_strength: float
    normal_format: str
    web_port: int
    worker_enabled: bool
    worker_scan_interval: int

    @staticmethod
    def load() -> "Config":
        missing = [n for n in _REQUIRED if not (os.environ.get(n) or "").strip()]
        if missing:
            raise ConfigError(
                "Faltan variables obligatorias (sin fallback): "
                + ", ".join(missing)
                + ". Copiá .env.example a .env y rellenalas."
            )

        # WORKER_ENABLED / WORKER_SCAN_INTERVAL: default hardcodeado, pero el
        # launcher del worker (start.bat / compose) los puede encender por env.
        # No hace falta ponerlos en .env.
        env_worker = (os.environ.get("WORKER_ENABLED") or "").strip().lower()
        worker_enabled = (
            env_worker in ("1", "true", "yes", "on") if env_worker else WORKER_ENABLED
        )
        try:
            scan = int(os.environ.get("WORKER_SCAN_INTERVAL") or WORKER_SCAN_INTERVAL)
        except ValueError:
            scan = WORKER_SCAN_INTERVAL

        return Config(
            pb_url=_require("PB_URL").rstrip("/"),
            pb_token=_require("PB_TOKEN"),
            pb_users=_require("PB_USERS"),
            pb_data=_require("PB_DATA"),
            normal_strength=NORMAL_STRENGTH,
            normal_format=NORMAL_FORMAT,
            web_port=WEB_PORT,
            worker_enabled=worker_enabled,
            worker_scan_interval=scan,
        )


# Singleton perezoso
_cfg: Config | None = None


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config.load()
    return _cfg
