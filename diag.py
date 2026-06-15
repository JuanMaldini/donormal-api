"""
diag.py — Diagnóstico one-off del estado real de PocketBase
Imprime:
  - Todos los usuarios de clothfigurator_user
  - Todos los registros de clothfigurator_data con su relation
  - Cuántos archivos tiene cada registro
  - Match contra el user que se logueó (juanmaldini7)
"""
import sys, os
sys.path.insert(0, "scripts")
from config import get_config
from pocketbase import PBAdmin, auth_with_password
import httpx, json

cfg = get_config()
admin = PBAdmin(cfg)
print(f"PB_URL = {cfg.pb_url}")
print(f"PB_USERS = {cfg.pb_users}")
print(f"PB_DATA  = {cfg.pb_data}")
print(f"PB_DATA2 = (no la usamos en dnormal-api, ojo: el web original tiene PB_DATA2)")
print()

# 1) Login con el user
try:
    res = auth_with_password("juanmaldini7@gmail.com", input("password: "), cfg)
    me = res["record"]
    me_id = me["id"]
    me_email = me.get("email") or me.get("username")
    print(f"Login OK: {me_email} -> {me_id}")
except Exception as e:
    print(f"Login FAIL: {e}")
    sys.exit(1)

# 2) Listar todos los registros
print("\n=== Registros en clothfigurator_data ===")
r = admin._client.get(
    f"/api/collections/{cfg.pb_data}/records",
    params={"page": 1, "perPage": 200},
)
r.raise_for_status()
data = r.json()
items = data.get("items", [])
print(f"Total registros: {data.get('totalItems')}\n")

for rec in items:
    rid = rec.get("id")
    rel = rec.get("relation")
    files = rec.get("file") or []
    files_n = len(files) if isinstance(files, list) else 0
    normals = [f for f in (files if isinstance(files, list) else []) if "_normal" in f.lower()]
    is_mine = "?" 
    if isinstance(rel, str):
        is_mine = "MIO" if rel == me_id else f"relation={rel!r}"
    elif isinstance(rel, list):
        is_mine = "MIO" if me_id in rel else f"relation={rel!r}"
    else:
        is_mine = f"relation={rel!r} (tipo={type(rel).__name__})"
    print(f"  {rid}  files={files_n}  normals={len(normals)}  {is_mine}")
    if os.environ.get("VERBOSE"):
        for f in (files if isinstance(files, list) else [])[:6]:
            print(f"      - {f}")
        if len(files) > 6:
            print(f"      ... (+{len(files)-6} más)")

# 3) Verificar match para MI user
print(f"\n=== Match para {me_id} ({me_email}) ===")
# Variante A: string
r1 = admin._client.get(
    f"/api/collections/{cfg.pb_data}/records",
    params={"filter": f'relation = "{me_id}"', "perPage": 1},
)
print(f"  filter 'relation = \"{me_id}\"': {r1.status_code} items={len(r1.json().get('items', []))}")
# Variante B: relation.id
r2 = admin._client.get(
    f"/api/collections/{cfg.pb_data}/records",
    params={"filter": f'relation.id ?= "{me_id}"', "perPage": 1},
)
print(f"  filter 'relation.id ?= \"{me_id}\"': {r2.status_code} items={len(r2.json().get('items', []))}")

# 4) Mostrar un sample de archivos
print("\n=== Sample archivos del registro 0 ===")
if items:
    rec0 = items[0]
    files = rec0.get("file") or []
    print(f"  record {rec0['id']} tiene {len(files)} archivos")
    for f in files[:15]:
        print(f"    - {f}")
    if len(files) > 15:
        print(f"    ... (+{len(files)-15} más)")
