"""
queue_manager.py — Cola de generación de normales (en RAM)
==========================================================
- Consumidor ÚNICO y secuencial: procesa de a una textura, serializa las
  escrituras a PocketBase (lo mantiene limpio, sin choques).
- Dedupe: una misma textura no se encola dos veces.
- Idempotente: antes de generar, RE-VALIDA contra PocketBase fresco que la
  normal siga sin existir. Aunque algo se encole de más, nunca duplica.

El estado vive solo en memoria. Si el contenedor reinicia, un "Refresh" en el
dashboard recalcula todo desde PocketBase (lo que no tiene normal vuelve a
estar LISTA). Decisión acordada: cola en RAM, un solo lugar (el backend).

Estados expuestos por textura:
    READY       sin normal, procesable
    QUEUED      esperando en la cola
    PROCESSING  generándose ahora
    HAS_NORMAL  ya tiene su _normal (no procesable)
    ERROR       el último intento falló (se puede reintentar)
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Callable

from config import Config
from logs import get_logger
from normalmap import bump_to_normal_bytes, is_normal_name
from pocketbase import PBAdmin, PBError, expected_normal_name

log = get_logger()

READY = "READY"
QUEUED = "QUEUED"
PROCESSING = "PROCESSING"
HAS_NORMAL = "HAS_NORMAL"
ERROR = "ERROR"


@dataclass
class _Job:
    user_id: str
    record_id: str
    filename: str


@dataclass
class _Runtime:
    """Estado en RAM por (user_id, filename) mientras está en cola/proceso."""
    state: str
    error: str | None = None


@dataclass
class NormalQueue:
    admin: PBAdmin
    cfg: Config
    _q: "queue.Queue[_Job]" = field(default_factory=queue.Queue)
    _runtime: dict[tuple[str, str], _Runtime] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _started: bool = False

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        t = threading.Thread(target=self._run, name="normal-queue", daemon=True)
        t.start()
        log.info("Cola de normales iniciada (consumidor único).")

    # ------------------------------------------------------------------ #
    def enqueue(self, user_id: str, record_id: str, filename: str) -> str:
        """Intenta encolar. Devuelve el estado resultante o lanza ValueError.

        Reglas de denegación:
          - el archivo ya es una normal (_normal)
          - ya está EN_COLA o PROCESANDO
        El chequeo de "ya tiene normal" lo hace quien llama (tiene el registro)
        y además lo re-valida el consumidor antes de generar.
        """
        if is_normal_name(filename):
            raise ValueError("Ese archivo ya es una normal; no se procesa.")
        key = (user_id, filename)
        with self._lock:
            rt = self._runtime.get(key)
            if rt and rt.state in (QUEUED, PROCESSING):
                raise ValueError(f"Ya está {rt.state.lower()}.")
            self._runtime[key] = _Runtime(state=QUEUED)
        self._q.put(_Job(user_id=user_id, record_id=record_id, filename=filename))
        self.start()
        log.info("Encolada: %s (user %s)", filename, user_id)
        return QUEUED

    def runtime_state(self, user_id: str, filename: str) -> _Runtime | None:
        with self._lock:
            return self._runtime.get((user_id, filename))

    def stats(self) -> dict:
        with self._lock:
            counts = {QUEUED: 0, PROCESSING: 0, ERROR: 0}
            for rt in self._runtime.values():
                counts[rt.state] = counts.get(rt.state, 0) + 1
            return {"pending": self._q.qsize(), **counts}

    def clear_done_for(self, user_id: str, filename: str) -> None:
        """Olvida el estado en RAM (tras éxito cae a la verdad de PB)."""
        with self._lock:
            self._runtime.pop((user_id, filename), None)

    # ------------------------------------------------------------------ #
    def _set(self, key: tuple[str, str], state: str, error: str | None = None) -> None:
        with self._lock:
            self._runtime[key] = _Runtime(state=state, error=error)

    def _run(self) -> None:
        while True:
            job = self._q.get()
            key = (job.user_id, job.filename)
            self._set(key, PROCESSING)
            try:
                self._process(job)
                # Éxito: borramos el estado en RAM; en el próximo refresh
                # PocketBase reportará HAS_NORMAL.
                with self._lock:
                    self._runtime.pop(key, None)
            except Exception as exc:  # noqa: BLE001 - registramos cualquier fallo
                log.error("Fallo procesando %s: %s", job.filename, exc)
                self._set(key, ERROR, str(exc))
            finally:
                self._q.task_done()

    def _process(self, job: _Job) -> None:
        # RE-VALIDACIÓN contra PB fresco (idempotencia / sin duplicados).
        record = self.admin.get_record_for_user(job.user_id)
        if not record:
            raise PBError("El registro del usuario ya no existe.")
        for pair in PBAdmin.pairs(record):
            if pair.texture == job.filename and pair.normal:
                log.info("Saltada (ya tiene normal): %s -> %s", job.filename, pair.normal)
                return
        if job.filename not in PBAdmin.files_of(record):
            raise PBError(f"La textura ya no está en el registro: {job.filename}")

        data = self.admin.download_file(record["id"], job.filename)
        out = bump_to_normal_bytes(
            data, strength=self.cfg.normal_strength, output_format=self.cfg.normal_format
        )
        name = expected_normal_name(job.filename, self.cfg.normal_format)
        ctype = "image/png" if self.cfg.normal_format == "png" else "image/x-exr"
        self.admin.append_file(record["id"], name, out, content_type=ctype)
        log.info("Normal creada: %s -> %s", job.filename, name)


# ----------------------------------------------------------------------- #
def compute_state(
    nq: NormalQueue, user_id: str, texture: str, has_normal: bool
) -> tuple[str, str | None]:
    """Estado final de una textura combinando verdad de PB + cola en RAM."""
    rt = nq.runtime_state(user_id, texture)
    if rt and rt.state in (QUEUED, PROCESSING, ERROR):
        return rt.state, rt.error
    if has_normal:
        return HAS_NORMAL, None
    return READY, None
