"""
main.py
=======
Worker de normales. Un proceso, un consumidor, sin puerto y sin volumen.

Ciclo:

    gate      valida config y PocketBase. NO avanza hasta que este verde.
    recover   los jobs colgados en `processing` vuelven a `pending`.
    barrido   procesa todo lo pendiente que haya ahora mismo.
    loop      SSE (reaccion en ~2s) + barrido periodico (red de seguridad).

Por que las DOS cosas y no solo una:

  - Solo polling: el usuario sube una textura y espera hasta 5 minutos mirando
    "generando". Funciona, pero se siente roto.
  - Solo SSE: el realtime de PocketBase NO reenvia lo que paso mientras el
    worker estaba desconectado. Un deploy de 30 segundos y esas texturas no las
    procesa nadie nunca.

El SSE y el timer solo ENCOLAN. Procesar pasa en un unico hilo consumidor, asi
que nunca hay dos normales generandose a la vez ni dos escrituras compitiendo
por el mismo record.
"""

from __future__ import annotations

import queue
import threading
import time

import httpx

from config import (
    NORMAL_FORMAT,
    NORMAL_STRENGTH,
    POLL_INTERVAL,
    ConfigError,
    get_config,
)
from logs import get_logger
from normalmap import bump_to_normal_bytes, normal_output_name
from pb import PB, PBError, Texture

log = get_logger()

SWEEP = "__sweep__"

# Estados desde los que una textura es procesable. El vacio entra porque los
# select de PocketBase no tienen default en el schema.
PROCESSABLE = ("pending", "")


# --------------------------------------------------------------------------- #
# Un job                                                                      #
# --------------------------------------------------------------------------- #
def process(pb: PB, tex: Texture) -> bool:
    """Genera y sube la normal de una textura. True si la creo."""
    if tex.status not in PROCESSABLE or not tex.albedo:
        return False

    if not pb.claim(tex):
        log.info("[%s] ya la tomo otro, salteo", tex.id)
        return False

    try:
        raw = pb.download(tex.id, tex.albedo)
        out = bump_to_normal_bytes(
            raw, strength=NORMAL_STRENGTH, output_format=NORMAL_FORMAT
        )
        name = normal_output_name(tex.albedo, NORMAL_FORMAT)
        ctype = "image/png" if NORMAL_FORMAT == "png" else "image/x-exr"
        pb.finish(tex.id, name, out, content_type=ctype)
        log.info("[%s] normal lista: %s -> %s", tex.id, tex.albedo, name)
        return True
    except (PBError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        pb.fail(tex, str(exc))
        return False


def sweep(pb: PB) -> int:
    """Procesa todo lo pendiente. Devuelve cuantas normales creo."""
    created = 0
    for tex in pb.pending():
        if process(pb, tex):
            created += 1
    if created:
        log.info("Barrido: %d normales creadas", created)
    return created


def process_by_id(pb: PB, record_id: str) -> None:
    """Procesa un record puntual, releyendolo primero.

    El evento del realtime trae una foto del record en el momento del cambio.
    Releerlo evita trabajar sobre datos viejos: entre el evento y este momento
    la textura pudo borrarse, o ya haberse procesado por el barrido.
    """
    tex = pb.get(record_id)
    if tex:
        process(pb, tex)


# --------------------------------------------------------------------------- #
# Productores                                                                 #
# --------------------------------------------------------------------------- #
def sse_producer(pb: PB, work: queue.Queue) -> None:
    """Escucha el realtime y encola ids. Reconecta sola.

    Cada reconexion encola un barrido: si el corte duro algo, el realtime no
    reenvia nada de lo que paso mientras tanto y el barrido es lo unico que lo
    recupera.
    """
    backoff = 1
    while True:
        try:
            for event in pb.realtime():
                backoff = 1
                record = event.get("record") or {}
                action = event.get("action")
                if action in ("create", "update") and record.get("id"):
                    work.put(record["id"])
        except (PBError, httpx.HTTPError) as exc:
            log.warning("Realtime caido (%s). Reintento en %ds", exc, backoff)
        except Exception as exc:  # noqa: BLE001 - el hilo no puede morir
            log.error("Realtime error inesperado: %s. Reintento en %ds", exc, backoff)

        time.sleep(backoff)
        backoff = min(backoff * 2, 60)
        work.put(SWEEP)


def poll_producer(work: queue.Queue) -> None:
    """Encola un barrido cada POLL_INTERVAL. La red de seguridad."""
    while True:
        time.sleep(POLL_INTERVAL)
        work.put(SWEEP)


# --------------------------------------------------------------------------- #
# Arranque                                                                    #
# --------------------------------------------------------------------------- #
def gate(pb: PB) -> None:
    """No retorna hasta que PocketBase responde y el login funciona."""
    attempt = 0
    while True:
        attempt += 1
        try:
            pb.check()
            log.info("Setup OK: PocketBase accesible y worker autenticado.")
            return
        except (PBError, httpx.HTTPError) as exc:
            wait = min(5 * attempt, 60)
            log.error("Setup (intento %d): %s -- reintento en %ds",
                      attempt, exc, wait)
            time.sleep(wait)


def main() -> int:
    try:
        cfg = get_config()
    except ConfigError as exc:
        log.error("%s", exc)
        return 1

    log.info("Worker de normales arrancando contra %s", cfg.pb_url)
    pb = PB(cfg)
    gate(pb)

    try:
        recovered = pb.recover_stale()
        if recovered:
            log.info("%d jobs colgados devueltos a la cola", recovered)
    except (PBError, httpx.HTTPError) as exc:
        log.warning("No se pudo recuperar jobs colgados: %s", exc)

    work: queue.Queue = queue.Queue()
    work.put(SWEEP)

    threading.Thread(target=sse_producer, args=(pb, work), daemon=True).start()
    threading.Thread(target=poll_producer, args=(work,), daemon=True).start()

    # Consumidor unico: todo el trabajo pasa por aca, de a uno.
    while True:
        item = work.get()
        try:
            if item == SWEEP:
                sweep(pb)
            else:
                process_by_id(pb, item)
        except (PBError, httpx.HTTPError) as exc:
            log.error("Fallo procesando %s: %s", item, exc)
        except Exception as exc:  # noqa: BLE001 - el consumidor no puede morir
            log.error("Error inesperado con %s: %s", item, exc)


if __name__ == "__main__":
    raise SystemExit(main())
