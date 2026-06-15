"""
pocketbase.py
=============
Cliente PocketBase mínimo sobre httpx. Dos roles:

1. auth_with_password(): login de un usuario final (colección PB_USERS).
   Se usa en el dashboard — el mismo login que el web de Clothfigurator.

2. PBAdmin: operaciones sobre la colección de datos (PB_DATA) usando el
   token de servicio PB_TOKEN como Bearer. Igual que hace el web
   (inyecta el Bearer en las requests a /collections/<data>/records).

Modelo de datos (deducido del web):
   - Registro por usuario en PB_DATA, con `relation` = userId.
   - Campo `file`: array de nombres de archivo (las texturas subidas).
   - Las normales se guardan en el MISMO array, nombradas con flag `_normal`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from config import Config, get_config
from logs import get_logger
from normalmap import is_normal_name, normal_output_name

log = get_logger()


class PBError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Login de usuario final (dashboard)                                          #
# --------------------------------------------------------------------------- #
def auth_with_password(identity: str, password: str, cfg: Config | None = None) -> dict:
    """Devuelve {token, record} o lanza PBError. Usa la colección PB_USERS."""
    cfg = cfg or get_config()
    url = f"{cfg.pb_url}/api/collections/{cfg.pb_users}/auth-with-password"
    try:
        r = httpx.post(
            url, json={"identity": identity, "password": password},
            timeout=20, trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise PBError(f"No se pudo contactar PocketBase: {exc}") from exc
    if r.status_code != 200:
        raise PBError("Credenciales inválidas o usuario no encontrado")
    return r.json()


# --------------------------------------------------------------------------- #
# Emparejado textura <-> normal                                               #
# --------------------------------------------------------------------------- #
@dataclass
class TexturePair:
    texture: str          # nombre de archivo de la textura (albedo)
    normal: str | None    # nombre de archivo de su normal, o None


def _noext(filename: str) -> str:
    return os.path.splitext(os.path.basename(filename))[0]


def normal_matches_texture(normal_name: str, texture_name: str) -> bool:
    """True si `normal_name` es la normal de `texture_name`.

    La normal se genera con normal_output_name(texture) y PocketBase le agrega
    su sufijo aleatorio después (_RRRRRRRRRR). Por eso comparamos contra la base
    esperada, tolerando ese sufijo. Funciona tanto para el caso `_normal` como
    para el reemplazo `bump -> normal`.
    """
    expected = _noext(normal_output_name(texture_name, "png"))
    nm = _noext(normal_name)
    return nm == expected or nm.startswith(expected + "_")


def expected_normal_name(texture_filename: str, output_format: str = "png") -> str:
    """Nombre de la normal a generar para una textura (antes del sufijo de PB)."""
    return normal_output_name(texture_filename, output_format)


# --------------------------------------------------------------------------- #
# Operaciones de servicio sobre la colección de datos                         #
# --------------------------------------------------------------------------- #
class PBAdmin:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or get_config()
        self._client = httpx.Client(
            base_url=self.cfg.pb_url,
            headers={"Authorization": f"Bearer {self.cfg.pb_token}"},
            timeout=60,
            trust_env=False,  # no heredar proxies del entorno
        )

    # -- conectividad / setup gate ------------------------------------------ #
    def ping(self) -> None:
        """Verifica que PocketBase responde y que el token sirve para PB_DATA."""
        try:
            r = self._client.get("/api/health")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise PBError(f"PocketBase no responde en {self.cfg.pb_url}: {exc}") from exc
        r = self._client.get(
            f"/api/collections/{self.cfg.pb_data}/records", params={"perPage": 1}
        )
        if r.status_code in (401, 403):
            raise PBError(
                f"PB_TOKEN no autoriza el acceso a la colección {self.cfg.pb_data}"
            )
        r.raise_for_status()

    # -- registros ---------------------------------------------------------- #
    def get_record_for_user(self, user_id: str) -> dict | None:
        """Registro de PB_DATA asignado al usuario (o None).

        La `relation` puede ser:
          - string con el id del user guardado a mano, o
          - campo relation de PocketBase (que se filtra con `relation.id`).
        Probamos ambas variantes con fallback, igual que hace
        Clothfigurator_web/src/utils/pocketbaseUserData.ts, para no
        depender de cómo esté armada la colección.
        """
        for filt in (f'relation = "{user_id}"', f'relation.id ?= "{user_id}"'):
            try:
                r = self._client.get(
                    f"/api/collections/{self.cfg.pb_data}/records",
                    params={"filter": filt, "perPage": 1},
                )
            except httpx.HTTPError as exc:
                log.debug("get_record_for_user: HTTP error con %s: %s", filt, exc)
                continue
            if r.status_code != 200:
                log.debug("get_record_for_user: status %s con %s", r.status_code, filt)
                continue
            items = r.json().get("items", [])
            if items:
                log.debug("get_record_for_user: match con filtro %s", filt)
                return items[0]
        return None

    def iter_all_records(self) -> list[dict]:
        """Todos los registros de PB_DATA (para el worker fase 2)."""
        out: list[dict] = []
        page = 1
        while True:
            r = self._client.get(
                f"/api/collections/{self.cfg.pb_data}/records",
                params={"page": page, "perPage": 200},
            )
            r.raise_for_status()
            data = r.json()
            out.extend(data.get("items", []))
            if page >= data.get("totalPages", 1):
                break
            page += 1
        return out

    @staticmethod
    def files_of(record: dict) -> list[str]:
        files = record.get("file")
        if isinstance(files, list):
            return [f for f in files if isinstance(f, str)]
        if isinstance(files, str) and files:
            return [files]
        return []

    @classmethod
    def pairs(cls, record: dict) -> list[TexturePair]:
        """Empareja cada textura con su normal (tolerando el sufijo de PB)."""
        files = cls.files_of(record)
        textures = [f for f in files if not is_normal_name(f)]
        normals = [f for f in files if is_normal_name(f)]
        result: list[TexturePair] = []
        for tex in textures:
            match = next(
                (nm for nm in normals if normal_matches_texture(nm, tex)), None
            )
            result.append(TexturePair(texture=tex, normal=match))
        return result

    # -- archivos ----------------------------------------------------------- #
    def _file_token(self) -> str:
        r = self._client.post("/api/files/token")
        if r.status_code == 200:
            return r.json().get("token", "")
        return ""

    def download_file(self, record_id: str, filename: str) -> bytes:
        token = self._file_token()
        params = {"token": token} if token else {}
        r = self._client.get(
            f"/api/files/{self.cfg.pb_data}/{record_id}/{filename}", params=params
        )
        r.raise_for_status()
        return r.content

    def append_file(self, record_id: str, filename: str, data: bytes,
                    content_type: str = "image/png") -> dict:
        files = {"file+": (filename, data, content_type)}
        r = self._client.patch(
            f"/api/collections/{self.cfg.pb_data}/records/{record_id}", files=files
        )
        if r.status_code != 200:
            raise PBError(f"Fallo al subir {filename}: {r.status_code} {r.text}")
        return r.json()

    def remove_file(self, record_id: str, filename: str) -> dict:
        # PocketBase: borrar un archivo concreto de un campo multi -> "file-"
        r = self._client.patch(
            f"/api/collections/{self.cfg.pb_data}/records/{record_id}",
            data={"file-": filename},
        )
        if r.status_code != 200:
            raise PBError(f"Fallo al borrar {filename}: {r.status_code} {r.text}")
        return r.json()

    def file_view_url(self, record_id: str, filename: str) -> str:
        """URL para previsualizar/enviar un archivo (incluye token de archivo)."""
        token = self._file_token()
        base = f"{self.cfg.pb_url}/api/files/{self.cfg.pb_data}/{record_id}/{filename}"
        return f"{base}?token={token}" if token else base

    def close(self) -> None:
        self._client.close()
