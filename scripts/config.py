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
# Usamos encoding="utf-8-sig" para que un eventual BOM UTF-8 al inicio del
# archivo no rompa la primera variable (PB_URL solía quedar vacía por eso).
try:
    from dotenv import load_dotenv

    load_dotenv(encoding="utf-8-sig")
except Exception:  # pragma: no cover - dotenv es opcional
    pass


class ConfigError(RuntimeError):
    """Falta una variable obligatoria o tiene un valor inválido."""


# Mínimo razonable para un JWT de PocketBase (arranca con "eyJ..." y mide >100).
# Bajamos a 30 para no romper con tokens más cortos, pero igual descartamos
# placeholders típicos.
_MIN_TOKEN_LEN = 30

# Placeholders comunes que la gente deja sin querer.
_TOKEN_PLACEHOLDERS = frozenset({
    "changeme", "change-me", "todo", "fixme",
    "xxx", "xxxx", "xxxxxx",
    "placeholder", "your_token", "your-token", "token_here", "put_your_token",
    "example", "sample", "test", "demo", "1234", "12345678",
})


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
    if name == "PB_TOKEN":
        if len(value) < _MIN_TOKEN_LEN:
            raise ConfigError(
                f"PB_TOKEN parece inválido (muy corto: {len(value)} chars, "
                f"mínimo esperado {_MIN_TOKEN_LEN}). "
                f"Copiá el token real desde PocketBase."
            )
        if value.lower() in _TOKEN_PLACEHOLDERS:
            raise ConfigError(
                f"PB_TOKEN parece un placeholder ({value!r}). "
                f"Reemplazalo por el token real de PocketBase."
            )
    if name == "PB_URL":
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ConfigError(
                f"PB_URL debe empezar con http:// o https:// (recibido: {value!r})"
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


# Variable opcional: si está, el dashboard habilita el modo admin
# (ver /api/admin/* en frontend/app.py).
# No es obligatoria: si está vacía o no existe, el modo admin queda deshabilitado.


# Singleton perezoso
_cfg: Config | None = None


def get_config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = Config.load()
    return _cfg
