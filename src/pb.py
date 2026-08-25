"""
pb.py
=====
Cliente PocketBase del worker de normales.

Reemplaza al `pocketbase.py` viejo, que estaba escrito contra el modelo de
datos anterior: UN record por usuario con un array `file[]` que mezclaba
modelos, texturas y normales, y el vinculo textura<->normal deducido del
sufijo `_normal` en el nombre del archivo (tolerando ademas el sufijo aleatorio
que PocketBase le agrega). Todo eso desaparecio: ahora la normal es un CAMPO
del record de su textura (`file_normal`), asi que no hay nada que emparejar.

Autenticacion: cuenta de servicio (un record de clothfigurator_users con
role "worker") por auth-with-password. NO un token fijo: PocketBase no tiene
API keys permanentes -- los tokens de impersonate no son renovables y vencen en
silencio, que es exactamente como se murio el PB_TOKEN anterior.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from config import COL_TEXTURES, COL_USERS, MAX_ATTEMPTS, STALE_CLAIM_SECONDS, Config
from logs import get_logger

log = get_logger()


class PBError(RuntimeError):
    pass


def _pb_now(offset_seconds: int = 0) -> str:
    """Timestamp en el formato que PocketBase guarda y compara."""
    moment = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%d %H:%M:%S.000Z")


@dataclass
class Texture:
    """Lo unico que el worker necesita de un record de textura."""

    id: str
    albedo: str
    status: str
    attempts: int

    @staticmethod
    def of(record: dict) -> "Texture":
        return Texture(
            id=record.get("id") or "",
            albedo=record.get("file_albedo") or "",
            status=record.get("normal_status") or "",
            attempts=int(record.get("attempts") or 0),
        )


class PB:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._token = ""
        # trust_env=False: no heredar proxies del entorno, que en un contenedor
        # suelen venir mal puestos y producen fallos que parecen de red.
        self._c = httpx.Client(base_url=cfg.pb_url, timeout=60, trust_env=False)

    # -- auth --------------------------------------------------------------- #
    def login(self) -> None:
        r = self._c.post(
            f"/api/collections/{COL_USERS}/auth-with-password",
            json={
                "identity": self.cfg.worker_email,
                "password": self.cfg.worker_password,
            },
        )
        if r.status_code != 200:
            raise PBError(
                f"Login del worker rechazado ({r.status_code}). Revisa "
                f"PB_WORKER_EMAIL / PB_WORKER_PASSWORD y que el usuario exista "
                f"en {COL_USERS}."
            )
        self._token = r.json().get("token") or ""
        if not self._token:
            raise PBError("PocketBase acepto el login pero no devolvio token.")

    def _headers(self) -> dict:
        return {"Authorization": self._token} if self._token else {}

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        """Request autenticada, con UN reintento si el token vencio.

        El token de PocketBase caduca. Sin esto, un worker que lleva dias
        levantado empieza a comer 401 y deja de procesar sin decir por que.
        """
        r = self._c.request(method, path, headers=self._headers(), **kw)
        if r.status_code == 401:
            log.info("Token vencido, reautenticando...")
            self.login()
            r = self._c.request(method, path, headers=self._headers(), **kw)
        return r

    # -- salud -------------------------------------------------------------- #
    def check(self) -> None:
        """Falla con un mensaje util si algo del setup no esta bien."""
        try:
            r = self._c.get("/api/health")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise PBError(
                f"PocketBase no responde en {self.cfg.pb_url}: {exc}"
            ) from exc

        self.login()

        r = self._request(
            "GET", f"/api/collections/{COL_TEXTURES}/records", params={"perPage": 1}
        )
        if r.status_code in (401, 403):
            raise PBError(
                f"El worker se autentico pero no puede leer {COL_TEXTURES}. "
                f"Revisa que su usuario tenga role=worker y la regla List de "
                f"la coleccion."
            )
        r.raise_for_status()

    # -- lectura ------------------------------------------------------------ #
    def pending(self, limit: int = 200) -> list[Texture]:
        """Texturas esperando su normal.

        El caso normal_status vacio entra a proposito: los campos select de
        PocketBase NO tienen valor por defecto en el schema, asi que un record
        creado sin mandar el campo queda vacio. Sin esta rama esas texturas no
        las encuentra nadie y no se convierten nunca.
        """
        flt = '(normal_status="pending" || normal_status="") && file_albedo!=""'
        r = self._request(
            "GET",
            f"/api/collections/{COL_TEXTURES}/records",
            params={"filter": flt, "perPage": limit, "sort": "created"},
        )
        r.raise_for_status()
        return [Texture.of(item) for item in r.json().get("items", [])]

    def get(self, record_id: str) -> Texture | None:
        r = self._request(
            "GET", f"/api/collections/{COL_TEXTURES}/records/{record_id}"
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return Texture.of(r.json())

    def recover_stale(self) -> int:
        """Devuelve a pending los jobs que quedaron colgados en processing.

        Pasa cuando el contenedor muere a mitad de un job (deploy, OOM, reinicio
        del host). Sin esto la textura queda trabada en processing para siempre:
        el filtro de pending no la ve y nadie la vuelve a reclamar.
        """
        cutoff = _pb_now(-STALE_CLAIM_SECONDS)
        flt = (
            f'normal_status="processing" && '
            f'(claimed_at<"{cutoff}" || claimed_at="")'
        )
        r = self._request(
            "GET",
            f"/api/collections/{COL_TEXTURES}/records",
            params={"filter": flt, "perPage": 200},
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        for item in items:
            self._patch(item["id"], {"normal_status": "pending"})
            log.info("[%s] job colgado -> vuelve a pending", item["id"])
        return len(items)

    # -- escritura ---------------------------------------------------------- #
    def _patch(self, record_id: str, data: dict) -> httpx.Response:
        r = self._request(
            "PATCH",
            f"/api/collections/{COL_TEXTURES}/records/{record_id}",
            json=data,
        )
        if r.status_code != 200:
            raise PBError(f"PATCH {record_id} fallo: {r.status_code} {r.text}")
        return r

    def claim(self, tex: Texture) -> bool:
        """Marca la textura como processing. False si otro la agarro antes.

        Relee el record despues de escribir. PocketBase no tiene update
        condicional, asi que esto no es un compare-and-swap: con UN worker
        (que es el diseno) alcanza, y si algun dia hay dos, esta relectura es
        el lugar donde meter el desempate.
        """
        self._patch(
            tex.id, {"normal_status": "processing", "claimed_at": _pb_now()}
        )
        fresh = self.get(tex.id)
        return bool(fresh and fresh.status == "processing")

    def finish(
        self,
        record_id: str,
        filename: str,
        data: bytes,
        content_type: str = "image/png",
    ) -> None:
        """Sube la normal y marca done EN LA MISMA request.

        Que sea una sola es lo que evita el estado intermedio "el archivo esta
        subido pero el status sigue en processing", donde un reinicio en el
        momento justo dejaria la textura para reprocesar y subir la normal dos
        veces.
        """
        r = self._request(
            "PATCH",
            f"/api/collections/{COL_TEXTURES}/records/{record_id}",
            files={"file_normal": (filename, data, content_type)},
            data={"normal_status": "done", "attempts": "0", "error_log": ""},
        )
        if r.status_code != 200:
            raise PBError(f"Subida de {filename} fallo: {r.status_code} {r.text}")

    def fail(self, tex: Texture, reason: str) -> None:
        """Suma un intento. Vuelve a la cola, o se rinde a los MAX_ATTEMPTS."""
        attempts = tex.attempts + 1
        if attempts < MAX_ATTEMPTS:
            status = "pending"
            log.warning(
                "[%s] intento %d/%d -> vuelve a la cola: %s",
                tex.id,
                attempts,
                MAX_ATTEMPTS,
                reason,
            )
        else:
            status = "error"
            log.error(
                "[%s] intento %d/%d -> se rinde: %s",
                tex.id,
                attempts,
                MAX_ATTEMPTS,
                reason,
            )
        self._patch(
            tex.id,
            {
                "normal_status": status,
                "attempts": attempts,
                "error_log": reason[:2000],
            },
        )

    # -- archivos ----------------------------------------------------------- #
    def download(self, record_id: str, filename: str) -> bytes:
        """Baja un archivo de la coleccion de texturas.

        Sin token de archivo: los file fields quedaron SIN protected, que es
        lo que hace que el visitante del link publico y Unreal (que no arrastra
        ninguna sesion) puedan leerlos.
        """
        r = self._c.get(f"/api/files/{COL_TEXTURES}/{record_id}/{filename}")
        if r.status_code != 200:
            raise PBError(f"No se pudo bajar {filename}: {r.status_code}")
        return r.content

    # -- realtime ----------------------------------------------------------- #
    def realtime(self):
        """Generador de eventos de clothfigurator_textures via SSE.

        Corta sola si la conexion se cae; reconectar es responsabilidad de
        main.py, que ademas hace un barrido al reconectar -- el realtime de
        PocketBase NO reenvia lo que paso mientras estabas desconectado.
        """
        headers = {**self._headers(), "Accept": "text/event-stream"}
        with self._c.stream(
            "GET",
            "/api/realtime",
            headers=headers,
            timeout=httpx.Timeout(None, connect=30),
        ) as resp:
            resp.raise_for_status()
            event = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    payload = line[5:].strip()
                    if event == "PB_CONNECT":
                        self._subscribe(json.loads(payload).get("clientId", ""))
                        log.info("Realtime conectado a %s", COL_TEXTURES)
                    elif event == COL_TEXTURES and payload:
                        yield json.loads(payload)

    def _subscribe(self, client_id: str) -> None:
        if not client_id:
            raise PBError("PB_CONNECT sin clientId")
        r = self._request(
            "POST",
            "/api/realtime",
            json={"clientId": client_id, "subscriptions": [COL_TEXTURES]},
        )
        if r.status_code not in (200, 204):
            raise PBError(f"No se pudo suscribir: {r.status_code} {r.text}")

    def close(self) -> None:
        self._c.close()
