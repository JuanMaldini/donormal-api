"""
export_legacy.py -- baja TODO lo de la PocketBase vieja a disco.

Se corre UNA vez. Deja una carpeta local con los archivos y un manifest.json
que dice, por archivo, a que coleccion nueva va y con que estado entra.

Por que existe: sin esto, cada vez que quisieras reintentar el import contra
un esquema distinto tendrias que volver a bajar todo. Con la carpeta local
podes correr import_new.py tantas veces como haga falta -- contra una
PocketBase local primero, contra la VPS al final -- sin depender de que la
instancia vieja siga viva.

    python tools/export_legacy.py [--url URL] [--out DIR]

No necesita credenciales: la coleccion `clothfigurator_data` de la instancia
vieja tiene la regla List publica. (Eso es, de paso, el agujero que el esquema
nuevo cierra: cualquiera podia bajarse los assets de todos los usuarios.)

NO entra en la imagen Docker del worker. Es de un solo uso y habla con una base
distinta; no hay motivo para darle al worker credenciales de dos instancias.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

LEGACY_URL = "https://pocketbase.vmoliver.cloud"
LEGACY_COLLECTION = "clothfigurator_data"

TEXTURE_EXTS = {"jpg", "jpeg", "png"}
CAD_EXTS = {"dwg", "dxf", "step", "stp", "iges", "igs"}
VIEWABLE_EXTS = {"glb", "gltf", "fbx"}

TIMEOUT = 60
CHUNK = 1024 * 1024


def ext_of(name: str) -> str:
    clean = name.split("?")[0]
    return clean.rsplit(".", 1)[-1].lower() if "." in clean else ""


def fetch_records(base: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        q = urllib.parse.urlencode({"perPage": 200, "page": page})
        url = f"{base}/api/collections/{LEGACY_COLLECTION}/records?{q}"
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            body = json.load(r)
        out.extend(body.get("items") or [])
        if page >= (body.get("totalPages") or 1):
            break
        page += 1
    return out


def parse_json_field(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def conversion_outputs(record: dict) -> set[str]:
    """Archivos que son SALIDA de una conversion.

    En el esquema nuevo el convertido es un campo del mismo record, no un
    archivo aparte, asi que estos no llevan record propio: irian como
    `file_glb`/`file_fbx` de su original. En la practica quedaron 0 -- las
    entradas de `json.conversions` de la base vieja apuntan a archivos que ya
    se borraron del `file[]` --, pero la logica queda porque el manifest tiene
    que ser correcto, no solo correcto para estos datos.
    """
    conv = parse_json_field(record.get("json")).get("conversions") or {}
    out = set()
    for entry in conv.values():
        if isinstance(entry, dict):
            name = entry.get("modelo_convertido")
            if isinstance(name, str) and name:
                out.add(name)
    return out


def classify(filename: str) -> tuple[str, str]:
    """(coleccion, estado inicial) para un archivo."""
    ext = ext_of(filename)
    if ext in TEXTURE_EXTS:
        return "textures", "pending"
    if ext in CAD_EXTS:
        return "models", "queued"
    if ext in VIEWABLE_EXTS:
        return "models", "ready"
    return "otros", ""


def download(base: str, record_id: str, filename: str, dest: str) -> int:
    url = "%s/api/files/%s/%s/%s" % (
        base, LEGACY_COLLECTION, record_id, urllib.parse.quote(filename))
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp, open(dest, "wb") as f:
        # Por bloques: hay FBX y DWG de decenas de MB y no hay motivo para
        # tenerlos enteros en memoria antes de escribir el primer byte.
        shutil.copyfileobj(resp, f, CHUNK)
    return os.path.getsize(dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=LEGACY_URL)
    ap.add_argument("--out", default="export")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    root = os.path.abspath(args.out)

    print("Origen : %s" % base)
    print("Destino: %s" % root)

    try:
        records = fetch_records(base)
    except urllib.error.HTTPError as e:
        print("No se pudo leer la coleccion: HTTP %s" % e.code)
        return 1
    except Exception as e:
        print("No se pudo contactar %s: %s" % (base, e))
        return 1

    print("Records: %d\n" % len(records))

    manifest = {"source": base, "collection": LEGACY_COLLECTION, "users": []}
    total_files = 0
    total_bytes = 0
    fallos = []

    for record in records:
        rid = record.get("id") or ""
        relation = record.get("relation")
        owner_legacy = relation[0] if isinstance(relation, list) and relation else relation
        files = [f for f in (record.get("file") or []) if isinstance(f, str) and f]
        salidas = conversion_outputs(record)

        user_dir = os.path.join(root, str(owner_legacy or rid))
        entry = {"legacy_record": rid, "legacy_owner": owner_legacy,
                 "dir": os.path.relpath(user_dir, root), "assets": []}

        print("Record %s (owner %s): %d archivos" % (rid, owner_legacy, len(files)))

        for name in files:
            if name in salidas:
                # Se salta: es el convertido de otro, no un modelo propio.
                print("  omitido (salida de conversion): %s" % name)
                continue

            kind, status = classify(name)
            if kind == "otros":
                print("  omitido (extension no soportada): %s" % name)
                continue

            dest_dir = os.path.join(user_dir, kind)
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, name)

            try:
                size = download(base, rid, name, dest)
            except Exception as e:
                print("  FALLO %s: %s" % (name, e))
                fallos.append(name)
                continue

            total_files += 1
            total_bytes += size
            entry["assets"].append({
                "file": name,
                "path": os.path.relpath(dest, root).replace("\\", "/"),
                "collection": kind,
                "status": status,
                "ext": ext_of(name),
                "bytes": size,
            })
            print("  %-9s %-7s %8.2f MB  %s"
                  % (kind, status, size / 1048576.0, name))

        manifest["users"].append(entry)

    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    print("\n%d archivos, %.1f MB" % (total_files, total_bytes / 1048576.0))
    if fallos:
        print("Fallaron %d: %s" % (len(fallos), ", ".join(fallos)))
    print("Manifest: %s" % os.path.join(root, "manifest.json"))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
