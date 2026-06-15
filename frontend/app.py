"""
app.py — Backend del dashboard de donormal (FastAPI)
====================================================
- Sirve el dashboard estático (static/index.html).
- Login con el MISMO usuario de Clothfigurator_web (colección PB_USERS).
- Lista las texturas del usuario con su ESTADO (cola + verdad de PocketBase).
- Encola la generación de normales (on-demand una a una, o auto-procesar todo).
- Borra normales (por ítem o todas).
- Hace de proxy de las imágenes (descarga protegida desde PocketBase).
- Expone la pareja {albedoURL, normalURL} lista para enviar (Unreal = futuro).
- Expone el log para el panel en vivo.
- (opcional) Modo ADMIN: si ADMIN_TOKEN está en .env, permite ver y
  procesar las texturas de TODOS los usuarios. Se activa con /api/admin/login.

El procesamiento usa la cola (scripts/queue_manager.py) y el algoritmo
horneado (scripts/normalmap.py).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import get_config
from logs import get_logger, tail_log
from normalmap import IMAGE_EXTS, is_image_name, is_normal_name
from pocketbase import PBAdmin, PBError, auth_with_password
from queue_manager import NormalQueue, compute_state

log = get_logger()
cfg = get_config()  # falla acá si faltan vars obligatorias (sin fallback)

app = FastAPI(title="donormal dashboard")
admin = PBAdmin(cfg)
nq = NormalQueue(admin=admin, cfg=cfg)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# --------------------------------------------------------------------------- #
# Admin: token opcional en .env (ADMIN_TOKEN=...). Si está vacío, el modo     #
# admin no está disponible (los endpoints /api/admin/* devuelven 404).        #
# --------------------------------------------------------------------------- #
ADMIN_TOKEN = (os.environ.get("ADMIN_TOKEN") or "").strip()
ADMIN_ENABLED = bool(ADMIN_TOKEN)
if ADMIN_ENABLED:
    log.info("Modo admin habilitado (ADMIN_TOKEN configurado).")
else:
    log.info("Modo admin deshabilitado (ADMIN_TOKEN vacío).")


# --------------------------------------------------------------------------- #
# Auth: validamos el token de usuario contra PocketBase en cada request        #
# --------------------------------------------------------------------------- #
def current_user(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Falta token")
    try:
        r = httpx.post(
            f"{cfg.pb_url}/api/collections/{cfg.pb_users}/auth-refresh",
            headers={"Authorization": token},
            timeout=20,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"PocketBase no responde: {exc}")
    if r.status_code != 200:
        raise HTTPException(401, "Sesión inválida o expirada")
    return r.json()["record"]


def admin_ok(x_admin_token: str = Header(default="", alias="X-Admin-Token")) -> bool:
    """True si el header X-Admin-Token coincide con el ADMIN_TOKEN del .env."""
    if not ADMIN_ENABLED:
        return False
    return x_admin_token.strip() == ADMIN_TOKEN


def _require_admin(x_admin_token: str = Header(default="", alias="X-Admin-Token")) -> None:
    if not ADMIN_ENABLED:
        raise HTTPException(404, "Modo admin deshabilitado")
    if x_admin_token.strip() != ADMIN_TOKEN:
        raise HTTPException(401, "Token admin inválido")


def _require_record(user: dict) -> dict:
    record = admin.get_record_for_user(user["id"])
    if not record:
        raise HTTPException(404, "No tenés registro de datos en PocketBase")
    return record


# --------------------------------------------------------------------------- #
# Modelos                                                                      #
# --------------------------------------------------------------------------- #
class LoginBody(BaseModel):
    identity: str
    password: str


class FileBody(BaseModel):
    filename: str


class AdminLoginBody(BaseModel):
    token: str


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #
@app.post("/api/login")
def login(body: LoginBody):
    try:
        res = auth_with_password(body.identity, body.password, cfg)
    except PBError as exc:
        raise HTTPException(401, str(exc))
    rec = res["record"]
    log.info("Login OK: %s (%s)", rec.get("email") or rec.get("username"), rec["id"])
    return {"token": res["token"], "user": {"id": rec["id"],
            "email": rec.get("email"), "name": rec.get("name") or rec.get("username")}}


@app.get("/api/admin/status")
def admin_status():
    """Le dice al front si el modo admin está disponible (sin filtrar el token)."""
    return {"enabled": ADMIN_ENABLED}


@app.post("/api/admin/login")
def admin_login(body: AdminLoginBody):
    """Valida el token admin y lo devuelve al front (lo guarda en sessionStorage)."""
    if not ADMIN_ENABLED:
        raise HTTPException(404, "Modo admin deshabilitado")
    if body.token.strip() != ADMIN_TOKEN:
        raise HTTPException(401, "Token admin inválido")
    return {"ok": True, "token": body.token.strip()}


# --------------------------------------------------------------------------- #
# Texturas + estado (modo usuario)                                            #
# --------------------------------------------------------------------------- #
def _item_for(p, user_id: str) -> dict:
    state, err = compute_state(nq, user_id, p.texture, p.normal is not None)
    return {
        "texture": p.texture,
        "normal": p.normal,
        "has_normal": p.normal is not None,
        "is_image": is_image_name(p.texture),
        "state": state if is_image_name(p.texture) else "SKIPPED",
        "error": err,
    }


@app.get("/api/textures")
def list_textures(user=Depends(current_user)):
    record = admin.get_record_for_user(user["id"])
    if not record:
        return {"record_id": None, "items": [], "queue": nq.stats()}
    pairs = PBAdmin.pairs(record)
    items = [_item_for(p, user["id"]) for p in pairs]
    return {"record_id": record["id"], "items": items, "queue": nq.stats()}


@app.post("/api/enqueue")
def enqueue(body: FileBody, user=Depends(current_user)):
    record = _require_record(user)
    if body.filename not in PBAdmin.files_of(record):
        raise HTTPException(404, f"Textura no encontrada: {body.filename}")
    # Deny si ya tiene normal.
    for p in PBAdmin.pairs(record):
        if p.texture == body.filename and p.normal:
            raise HTTPException(409, f"Esa textura ya tiene normal: {p.normal}")
    try:
        state = nq.enqueue(user["id"], record["id"], body.filename)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "filename": body.filename, "state": state}


@app.post("/api/auto-process")
def auto_process(user=Depends(current_user)):
    """Encola TODAS las texturas procesables del usuario (botón peligroso)."""
    record = _require_record(user)
    queued = 0
    for p in PBAdmin.pairs(record):
        if p.normal:
            continue
        if not is_image_name(p.texture):
            continue
        try:
            nq.enqueue(user["id"], record["id"], p.texture)
            queued += 1
        except ValueError:
            pass  # ya en cola / procesando: se ignora
    log.info("Auto-procesar: %d texturas encoladas (user %s)", queued, user["id"])
    return {"ok": True, "queued": queued}


@app.get("/api/queue")
def queue_status(user=Depends(current_user)):
    return nq.stats()


# --------------------------------------------------------------------------- #
# Borrado de normales                                                         #
# --------------------------------------------------------------------------- #
@app.post("/api/normals/delete")
def delete_normal(body: FileBody, user=Depends(current_user)):
    record = _require_record(user)
    if not is_normal_name(body.filename):
        raise HTTPException(400, "Ese archivo no es una normal.")
    if body.filename not in PBAdmin.files_of(record):
        raise HTTPException(404, "Normal no encontrada.")
    try:
        admin.remove_file(record["id"], body.filename)
    except PBError as exc:
        raise HTTPException(500, str(exc))
    log.info("Normal borrada: %s (user %s)", body.filename, user["id"])
    return {"ok": True, "deleted": body.filename}


@app.post("/api/normals/delete-all")
def delete_all_normals(user=Depends(current_user)):
    """Borra TODAS las normales del usuario (botón peligroso)."""
    record = _require_record(user)
    normals = [f for f in PBAdmin.files_of(record) if is_normal_name(f)]
    deleted = 0
    for name in normals:
        try:
            admin.remove_file(record["id"], name)
            deleted += 1
        except PBError as exc:
            log.error("No se pudo borrar %s: %s", name, exc)
    log.info("Borrado masivo de normales: %d (user %s)", deleted, user["id"])
    return {"ok": True, "deleted": deleted}


# --------------------------------------------------------------------------- #
# Pareja albedo + normal (URLs con token, listas para enviar)                  #
# --------------------------------------------------------------------------- #
@app.get("/api/pair/{texture}")
def pair_urls(texture: str, user=Depends(current_user)):
    """Devuelve {albedoURL, normalURL} con token, igual que arma el web.

    Esto es lo que después consume Unreal (ver scripts/unreal_payload.py).
    """
    record = _require_record(user)
    files = PBAdmin.files_of(record)
    if texture not in files:
        raise HTTPException(404, "Textura no encontrada")
    normal = None
    for p in PBAdmin.pairs(record):
        if p.texture == texture:
            normal = p.normal
            break
    return {
        "albedo": texture,
        "normal": normal,
        "albedoURL": admin.file_view_url(record["id"], texture),
        "normalURL": admin.file_view_url(record["id"], normal) if normal else None,
    }


# --------------------------------------------------------------------------- #
# Proxy de archivos (para mostrar imágenes en el dashboard)                    #
# --------------------------------------------------------------------------- #
@app.get("/api/file/{filename}")
def get_file(filename: str, user=Depends(current_user)):
    record = admin.get_record_for_user(user["id"])
    if not record or filename not in PBAdmin.files_of(record):
        raise HTTPException(404, "Archivo no encontrado")
    try:
        content = admin.download_file(record["id"], filename)
    except PBError as exc:
        raise HTTPException(502, str(exc))
    ext = filename.rsplit(".", 1)[-1].lower()
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "webp": "image/webp", "bmp": "image/bmp", "tga": "image/x-tga"}.get(
        ext, "application/octet-stream"
    )
    return Response(content=content, media_type=media,
                    headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- #
# MODO ADMIN: ver y procesar texturas de TODOS los usuarios                   #
# --------------------------------------------------------------------------- #
def _resolve_user_for_record(record: dict) -> dict:
    """Dado un record de PB_DATA, devuelve {id, email, name} de su user.

    Si la relation es un relation-field de PB, se puede expandir. Si no,
    intentamos listar users de la colección PB_USERS y matchear.
    """
    relation = record.get("relation")
    user_id = None
    if isinstance(relation, str):
        user_id = relation or None
    elif isinstance(relation, list) and relation:
        user_id = relation[0]
    elif isinstance(relation, dict):
        # PocketBase expande relations a {id, ...} en algunos endpoints
        user_id = relation.get("id")

    if not user_id:
        return {"id": None, "email": "(sin asignar)", "name": "—"}

    # Lookup del user (no-op si PB no soporta filtrar por id, pero suele funcionar)
    try:
        r = admin._client.get(
            f"/api/collections/{cfg.pb_users}/records/{user_id}",
        )
        if r.status_code == 200:
            rec = r.json()
            return {
                "id": rec.get("id"),
                "email": rec.get("email") or rec.get("username") or "(sin email)",
                "name": rec.get("name") or rec.get("username") or "",
            }
    except Exception:  # noqa: BLE001 - si falla el lookup, caemos al fallback
        pass

    return {"id": user_id, "email": "(no resuelto)", "name": ""}


def _build_admin_item(record: dict, p) -> dict:
    state, err = compute_state(nq, _record_owner_key(record), p.texture, p.normal is not None)
    return {
        "record_id": record["id"],
        "texture": p.texture,
        "normal": p.normal,
        "has_normal": p.normal is not None,
        "is_image": is_image_name(p.texture),
        "state": state if is_image_name(p.texture) else "SKIPPED",
        "error": err,
    }


def _record_owner_key(record: dict) -> str:
    """Una key estable por registro para el estado en RAM de la cola."""
    rel = record.get("relation")
    if isinstance(rel, str):
        return f"rec:{record['id']}:{rel}"
    if isinstance(rel, list) and rel:
        return f"rec:{record['id']}:{rel[0]}"
    return f"rec:{record['id']}"


@app.get("/api/admin/overview")
def admin_overview(_: None = Depends(_require_admin)):
    """Lista TODOS los registros con sus texturas y normales, agrupados por user."""
    records = admin.iter_all_records()
    users: dict[str, dict] = {}
    for rec in records:
        owner = _resolve_user_for_record(rec)
        uid = owner.get("id") or f"orphan:{rec['id']}"
        if uid not in users:
            users[uid] = {
                "user": owner,
                "record_id": rec["id"],
                "items": [],
            }
        for p in PBAdmin.pairs(rec):
            users[uid]["items"].append(_build_admin_item(rec, p))
    return {"users": list(users.values()), "queue": nq.stats()}


@app.get("/api/admin/records/{record_id}/textures")
def admin_record_textures(record_id: str, _: None = Depends(_require_admin)):
    rec = admin._client.get(f"/api/collections/{cfg.pb_data}/records/{record_id}").json()
    items = [_build_admin_item(rec, p) for p in PBAdmin.pairs(rec)]
    return {"record_id": record_id, "user": _resolve_user_for_record(rec),
            "items": items, "queue": nq.stats()}


class AdminEnqueueBody(BaseModel):
    record_id: str
    filename: str


@app.post("/api/admin/enqueue")
def admin_enqueue(body: AdminEnqueueBody, _: None = Depends(_require_admin)):
    try:
        rec = admin._client.get(
            f"/api/collections/{cfg.pb_data}/records/{body.record_id}"
        )
        rec.raise_for_status()
        record = rec.json()
    except httpx.HTTPError as exc:
        raise HTTPException(404, f"Registro no encontrado: {exc}")
    if body.filename not in PBAdmin.files_of(record):
        raise HTTPException(404, f"Textura no encontrada: {body.filename}")
    for p in PBAdmin.pairs(record):
        if p.texture == body.filename and p.normal:
            raise HTTPException(409, f"Esa textura ya tiene normal: {p.normal}")
    owner_key = _record_owner_key(record)
    try:
        state = nq.enqueue(owner_key, record["id"], body.filename)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    log.info("Admin enqueue: %s (record %s)", body.filename, body.record_id)
    return {"ok": True, "filename": body.filename, "state": state}


@app.post("/api/admin/auto-process/{record_id}")
def admin_auto_process(record_id: str, _: None = Depends(_require_admin)):
    """Encola TODAS las texturas procesables de un registro (admin)."""
    try:
        rec = admin._client.get(
            f"/api/collections/{cfg.pb_data}/records/{record_id}"
        )
        rec.raise_for_status()
        record = rec.json()
    except httpx.HTTPError as exc:
        raise HTTPException(404, f"Registro no encontrado: {exc}")
    owner_key = _record_owner_key(record)
    queued = 0
    for p in PBAdmin.pairs(record):
        if p.normal:
            continue
        if not is_image_name(p.texture):
            continue
        try:
            nq.enqueue(owner_key, record["id"], p.texture)
            queued += 1
        except ValueError:
            pass
    log.info("Admin auto-procesar: %d encoladas (record %s)", queued, record_id)
    return {"ok": True, "queued": queued}


@app.post("/api/admin/normals/delete")
def admin_delete_normal(body: FileBody, record_id: str = Header(default="", alias="X-Record-Id"),
                        _: None = Depends(_require_admin)):
    if not record_id:
        raise HTTPException(400, "Falta X-Record-Id")
    try:
        rec = admin._client.get(
            f"/api/collections/{cfg.pb_data}/records/{record_id}"
        )
        rec.raise_for_status()
        record = rec.json()
    except httpx.HTTPError as exc:
        raise HTTPException(404, f"Registro no encontrado: {exc}")
    if not is_normal_name(body.filename):
        raise HTTPException(400, "Ese archivo no es una normal.")
    if body.filename not in PBAdmin.files_of(record):
        raise HTTPException(404, "Normal no encontrada.")
    try:
        admin.remove_file(record["id"], body.filename)
    except PBError as exc:
        raise HTTPException(500, str(exc))
    log.info("Admin: normal borrada %s (record %s)", body.filename, record_id)
    return {"ok": True, "deleted": body.filename}


@app.get("/api/admin/file/{record_id}/{filename}")
def admin_get_file(record_id: str, filename: str, _: None = Depends(_require_admin)):
    """Sirve cualquier archivo de cualquier registro (admin)."""
    try:
        rec = admin._client.get(
            f"/api/collections/{cfg.pb_data}/records/{record_id}"
        )
        rec.raise_for_status()
        record = rec.json()
    except httpx.HTTPError as exc:
        raise HTTPException(404, f"Registro no encontrado: {exc}")
    if filename not in PBAdmin.files_of(record):
        raise HTTPException(404, "Archivo no encontrado")
    try:
        content = admin.download_file(record["id"], filename)
    except PBError as exc:
        raise HTTPException(502, str(exc))
    ext = filename.rsplit(".", 1)[-1].lower()
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "webp": "image/webp", "bmp": "image/bmp", "tga": "image/x-tga"}.get(
        ext, "application/octet-stream"
    )
    return Response(content=content, media_type=media,
                    headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- #
# Logs / salud / estático                                                     #
# --------------------------------------------------------------------------- #
@app.get("/api/logs", response_class=PlainTextResponse)
def get_logs(lines: int = 200):
    return tail_log(lines)


@app.get("/api/health")
def health():
    try:
        admin.ping()
        return {"ok": True, "pb_url": cfg.pb_url, "data": cfg.pb_data,
                "admin_enabled": ADMIN_ENABLED}
    except PBError as exc:
        raise HTTPException(503, str(exc))


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
