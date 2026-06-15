"""
app.py — Backend del dashboard de donormal (FastAPI)
===================================================
- Sirve el dashboard estático (static/index.html).
- Login con el MISMO usuario de Clothfigurator_web (colección PB_USERS).
- Lista las texturas del usuario con su ESTADO (cola + verdad de PocketBase).
- Encola la generación de normales (on-demand una a una, o auto-procesar todo).
- Borra normales (por ítem o todas).
- Hace de proxy de las imágenes (descarga protegida desde PocketBase).
- Expone la pareja {albedoURL, normalURL} lista para enviar (Unreal = futuro).
- Expone el log para el panel en vivo.

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
from pocketbase import PBAdmin, PBError, auth_with_password
from queue_manager import HAS_NORMAL, NormalQueue, compute_state
from normalmap import is_normal_name

log = get_logger()
cfg = get_config()  # falla acá si faltan vars obligatorias (sin fallback)

app = FastAPI(title="donormal dashboard")
admin = PBAdmin(cfg)
nq = NormalQueue(admin=admin, cfg=cfg)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


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


# --------------------------------------------------------------------------- #
# Texturas + estado                                                           #
# --------------------------------------------------------------------------- #
@app.get("/api/textures")
def list_textures(user=Depends(current_user)):
    record = admin.get_record_for_user(user["id"])
    if not record:
        return {"record_id": None, "items": [], "queue": nq.stats()}
    pairs = PBAdmin.pairs(record)
    items = []
    for p in pairs:
        state, err = compute_state(nq, user["id"], p.texture, p.normal is not None)
        items.append({
            "texture": p.texture,
            "normal": p.normal,
            "has_normal": p.normal is not None,
            "state": state,
            "error": err,
        })
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
# Logs / salud / estático                                                     #
# --------------------------------------------------------------------------- #
@app.get("/api/logs", response_class=PlainTextResponse)
def get_logs(lines: int = 200):
    return tail_log(lines)


@app.get("/api/health")
def health():
    try:
        admin.ping()
        return {"ok": True, "pb_url": cfg.pb_url, "data": cfg.pb_data}
    except PBError as exc:
        raise HTTPException(503, str(exc))


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
