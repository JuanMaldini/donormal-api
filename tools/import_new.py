"""
import_new.py -- sube lo exportado a la PocketBase nueva.

Lee ./export/manifest.json (lo deja export_legacy.py) y crea UN RECORD POR
ASSET en la coleccion que corresponda, con el estado inicial que le toca:

    texturas  -> normal_status="pending"   (el worker les genera la normal)
    CAD       -> status="queued"           (3ds Max los convierte)
    FBX/GLB   -> status="ready"            (usables tal cual)

    python tools/import_new.py [--export export] [--dry-run] [--force]

Variables de entorno (las mismas del worker, mas dos):

    PB_URL, PB_USERS, PB_MODELS, PB_TEXTURES
    PB_IMPORT_EMAIL / PB_IMPORT_PASSWORD   <- el usuario que queda como OWNER

El owner NO puede ser la cuenta `worker`: los assets tienen que pertenecer a
la persona que los va a ver en el dashboard. La regla Create de las dos
colecciones exige `owner = @request.auth.id`, asi que el import entra como ese
usuario y crea sus propios records.

Es idempotente por `name`: un asset que ya existe para ese owner se saltea, asi
que se puede volver a correr sin duplicar. `--force` lo sube igual.

OJO: los 13 DWG entran como `queued`. Apenas termine, el panel de 3ds Max va a
tener 13 jobs esperando.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

TIMEOUT = 300


def env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        print("Falta la variable de entorno %s" % name)
        raise SystemExit(2)
    return value


def load_dotenv() -> None:
    """Carga .env de la raiz del repo si existe. Conveniencia local."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def request(url, method="GET", body=None, token=None, raw=None, ctype=None):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        data = raw
        headers["Content-Type"] = ctype
    if token:
        headers["Authorization"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        txt = resp.read().decode("utf-8", "replace")
        return resp.status, (json.loads(txt) if txt.strip() else {})


def multipart(fields: dict, files: dict):
    """multipart/form-data a mano: no hay requests en la stdlib."""
    boundary = "----cf" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                % (boundary, k, v)).encode("utf-8")
    for k, path in files.items():
        name = os.path.basename(path)
        ct = mimetypes.guess_type(name)[0] or "application/octet-stream"
        out += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                "Content-Type: %s\r\n\r\n" % (boundary, k, name, ct)).encode("utf-8")
        with open(path, "rb") as f:
            out += f.read()
        out += b"\r\n"
    out += ("--%s--\r\n" % boundary).encode("utf-8")
    return bytes(out), "multipart/form-data; boundary=" + boundary


def http_detail(e: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(e.read().decode("utf-8", "replace"))
    except Exception:
        return "HTTP %s" % e.code
    partes = []
    for k, v in (body.get("data") or {}).items():
        msg = v.get("message") if isinstance(v, dict) else str(v)
        partes.append("%s: %s" % (k, msg))
    return "HTTP %s %s%s" % (e.code, body.get("message") or "",
                             (" (" + "; ".join(partes) + ")") if partes else "")


def asset_display_name(filename: str) -> str:
    """Mismo criterio que el frontend: el nombre del archivo, sin ruta."""
    return os.path.basename(filename)


def exists(base, coll, token, owner, name) -> bool:
    flt = 'owner="%s" && name="%s"' % (owner, name.replace('"', '\\"'))
    q = urllib.parse.urlencode({"perPage": 1, "filter": flt})
    st, body = request("%s/api/collections/%s/records?%s" % (base, coll, q),
                       token=token)
    return bool(body.get("totalItems"))


def main() -> int:
    load_dotenv()

    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default="export")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="sube aunque ya exista un asset con ese nombre")
    args = ap.parse_args()

    base = env("PB_URL").rstrip("/")
    col_users = env("PB_USERS")
    col_models = env("PB_MODELS")
    col_textures = env("PB_TEXTURES")
    email = env("PB_IMPORT_EMAIL")
    password = env("PB_IMPORT_PASSWORD")

    root = os.path.abspath(args.export)
    manifest_path = os.path.join(root, "manifest.json")
    if not os.path.exists(manifest_path):
        print("No existe %s. Corre primero tools/export_legacy.py" % manifest_path)
        return 2
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    print("Destino: %s" % base)
    print("Origen : %s\n" % root)

    try:
        st, auth = request("%s/api/collections/%s/auth-with-password"
                           % (base, col_users), method="POST",
                           body={"identity": email, "password": password})
    except urllib.error.HTTPError as e:
        print("Login rechazado: %s" % http_detail(e))
        print("Revisa PB_IMPORT_EMAIL / PB_IMPORT_PASSWORD.")
        return 1

    token = auth.get("token") or ""
    owner = (auth.get("record") or {}).get("id") or ""
    rol = (auth.get("record") or {}).get("role") or ""
    print("Owner: %s (%s, role=%s)\n" % (email, owner, rol or "(vacio)"))

    if rol == "worker":
        print("AVISO: estas importando como la cuenta de servicio. Los assets")
        print("van a pertenecer al worker y no los va a ver ningun usuario en")
        print("el dashboard. Usa la cuenta real de la persona.\n")

    creados = {"textures": 0, "models": 0}
    saltados = 0
    fallos = []

    for user in manifest.get("users") or []:
        for asset in user.get("assets") or []:
            path = os.path.join(root, asset["path"])
            coll = col_textures if asset["collection"] == "textures" else col_models
            name = asset_display_name(asset["file"])

            if not os.path.exists(path):
                print("  FALTA en disco: %s" % asset["path"])
                fallos.append(asset["file"])
                continue

            if not args.force and not args.dry_run:
                try:
                    if exists(base, coll, token, owner, name):
                        print("  ya existe, salteo: %s" % name)
                        saltados += 1
                        continue
                except urllib.error.HTTPError as e:
                    print("  no se pudo comprobar duplicado (%s): sigo" % http_detail(e))

            if asset["collection"] == "textures":
                fields = {"owner": owner, "name": name,
                          "normal_status": asset["status"], "attempts": "0"}
                files = {"file_albedo": path}
            else:
                fields = {"owner": owner, "name": name,
                          "ext_original": asset["ext"],
                          "status": asset["status"], "attempts": "0"}
                files = {"file_original": path}

            etiqueta = "%-9s %-7s %s" % (asset["collection"], asset["status"], name)

            if args.dry_run:
                print("  [dry-run] %s" % etiqueta)
                continue

            payload, ctype = multipart(fields, files)
            try:
                st, rec = request("%s/api/collections/%s/records" % (base, coll),
                                  method="POST", raw=payload, ctype=ctype,
                                  token=token)
                creados[asset["collection"]] += 1
                print("  OK  %s  -> %s" % (etiqueta, rec.get("id")))
            except urllib.error.HTTPError as e:
                print("  FALLO %s: %s" % (etiqueta, http_detail(e)))
                fallos.append(asset["file"])
            except Exception as e:
                print("  FALLO %s: %s" % (etiqueta, e))
                fallos.append(asset["file"])

    print("\nTexturas creadas: %d" % creados["textures"])
    print("Modelos creados : %d" % creados["models"])
    if saltados:
        print("Salteados (ya existian): %d" % saltados)
    if fallos:
        print("Fallaron %d: %s" % (len(fallos), ", ".join(fallos)))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
