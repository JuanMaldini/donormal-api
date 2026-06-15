"""
worker.py — Procesamiento automático (FASE 2, apagado por defecto)
==================================================================
MVP actual: el procesamiento es on-demand desde el dashboard. Este worker
queda como esqueleto para cuando quieras "todo directo".

Comportamiento cuando WORKER_ENABLED=true (hoy hardcodeado en false):
  1. GATE DE ARRANQUE: valida .env y conecta a PocketBase. Si algo falla,
     reintenta y NO avanza hasta que todo esté verde.
  2. SCAN: recorre todos los registros de PB_DATA y genera la normal de
     cada textura que aún no la tenga (flag _normal).
  3. Repite el scan cada WORKER_SCAN_INTERVAL segundos.

  (El realtime de PocketBase para reaccionar a uploads/deletes al instante
   se puede sumar acá usando SSE sobre /api/realtime — TODO fase 2.1.)

Arranque manual:  python worker.py
"""

from __future__ import annotations

import time

from config import get_config
from logs import get_logger
from normalmap import bump_to_normal_bytes, is_image_name
from pocketbase import PBAdmin, PBError, expected_normal_name

log = get_logger()


def startup_gate(admin: PBAdmin, retries: int = 0) -> None:
    """No retorna hasta que PocketBase responde y el token funciona."""
    attempt = 0
    while True:
        attempt += 1
        try:
            admin.ping()
            log.info("Setup OK: PocketBase accesible y token válido.")
            return
        except PBError as exc:
            log.error("Setup gate (intento %d): %s", attempt, exc)
            if retries and attempt >= retries:
                raise
            time.sleep(5)


def process_record(admin: PBAdmin, cfg, record: dict) -> int:
    """Genera las normales faltantes de un registro. Devuelve cuántas creó."""
    created = 0
    for pair in PBAdmin.pairs(record):
        if pair.normal:
            continue
        if not is_image_name(pair.texture):
            log.info(
                "[%s] skip (no es imagen): %s", record["id"], pair.texture
            )
            continue
        try:
            data = admin.download_file(record["id"], pair.texture)
            out = bump_to_normal_bytes(
                data, strength=cfg.normal_strength, output_format=cfg.normal_format
            )
            name = expected_normal_name(pair.texture, cfg.normal_format)
            ctype = "image/png" if cfg.normal_format == "png" else "image/x-exr"
            admin.append_file(record["id"], name, out, content_type=ctype)
            log.info("[%s] normal creada: %s -> %s", record["id"], pair.texture, name)
            created += 1
        except (PBError, ValueError, RuntimeError) as exc:
            log.error("[%s] fallo con %s: %s", record["id"], pair.texture, exc)
    return created


def scan_once(admin: PBAdmin, cfg) -> int:
    total = 0
    for record in admin.iter_all_records():
        total += process_record(admin, cfg, record)
    log.info("Scan completo. Normales creadas en esta pasada: %d", total)
    return total


def main() -> int:
    cfg = get_config()
    admin = PBAdmin(cfg)

    if not cfg.worker_enabled:
        log.info("WORKER_ENABLED=false — worker en standby (modo on-demand). Saliendo.")
        return 0

    log.info("Worker arrancando…")
    startup_gate(admin)
    while True:
        try:
            scan_once(admin, cfg)
        except PBError as exc:
            log.error("Error en scan: %s", exc)
        time.sleep(cfg.worker_scan_interval)


if __name__ == "__main__":
    raise SystemExit(main())
