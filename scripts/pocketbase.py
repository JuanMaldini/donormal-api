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


class PBFilterBadRequest(PBError):
    """El filtro de PB no es valido para esta colección (HTTP 400)."""

    def __init__(self, filter_str: str, body: str) -> None:
        super().__init__(f"filtro invalido: {filter_str}")
        self.filter = filter_str
        self.body = body


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

    Convención: sin prefijo. La normal se genera como
    `normal_output_name(texture)` = `<root>_normal.png` (o `<root>` con
    bump->normal). PocketBase le agrega su sufijo aleatorio al subirla,
    así que la búsqueda es tolerante a ese sufijo (`<root>_normal.png`
    puede ser `<root>_normal_XXXXXXX.png`).

    Funciona también con el caso `bump -> normal` (ej: `chair_bump.jpg`
    genera `chair_normal.png` y la búsqueda matchea con tolerancia).
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
    # Page size y filtros replican la lógica de
    # Clothfigurator_web/src/utils/pocketbaseUserData.ts:getAssignedRecordByUser
    # para que el dashboard se comporte exactamente igual que el control panel
    # del web original.
    RELATION_LOOKUP_PAGE_SIZE = 50

    @staticmethod
    def _record_belongs_to_user(record: dict, user_id: str) -> bool:
        """True si el `relation` del record matchea user_id (string o array)."""
        rel = record.get("relation")
        if isinstance(rel, str):
            return rel.strip() == user_id
        if isinstance(rel, list):
            return any((isinstance(x, str) and x.strip() == user_id) for x in rel)
        return False

    def _list_by_filter(self, filt: str) -> list[dict]:
        r = self._client.get(
            f"/api/collections/{self.cfg.pb_data}/records",
            params={"filter": filt, "perPage": self.RELATION_LOOKUP_PAGE_SIZE},
        )
        if r.status_code == 400:
            # 400 = el filtro no es valido para esta colección (por ej. la
            # relation es un string y `relation.id ?= ...` no aplica). El web
            # hace lo mismo: catch de 400 -> fallback a la otra variante.
            raise PBFilterBadRequest(filt, r.text)
        r.raise_for_status()
        return r.json().get("items", [])

    def get_record_for_user(self, user_id: str) -> dict | None:
        """Registro de PB_DATA asignado al usuario (o None).

        Replica exactamente el flujo de
        Clothfigurator_web/src/utils/pocketbaseUserData.ts:
          1. Probar `relation.id ?= "X"` (campo relation de PocketBase).
          2. Si falla con 400 (la relación es string, no relation-field),
             probar `relation ?= "X"`.
          3. Validar que el primer record devuelto realmente pertenece al
             user (defensa contra records "huérfanos").
        """
        for label, filt in (
            ("relation.id", f'relation.id ?= "{user_id}"'),
            ("relation",    f'relation ?= "{user_id}"'),
        ):
            try:
                items = self._list_by_filter(filt)
            except PBFilterBadRequest as exc:
                log.debug("get_record_for_user: filtro %s dio 400, probando fallback", exc.filter)
                continue
            except httpx.HTTPError as exc:
                log.debug("get_record_for_user: HTTP error con %s: %s", label, exc)
                continue
            for rec in items:
                if self._record_belongs_to_user(rec, user_id):
                    log.debug("get_record_for_user: match con filtro %s", label)
                    return rec
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
