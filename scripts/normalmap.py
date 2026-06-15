"""
normalmap.py
============
Generador de normal maps a partir de una textura (bump/height map).

Replica EXACTAMENTE el algoritmo de MircoWerner/BumpToNormalMap, que es el
que usa tu proyecto Normal_creator por debajo. Diferencia clave: aquí el
algoritmo viene "horneado" en el contenedor (no se descarga nada en runtime
ni se hace pip install al vuelo), y usa opencv-python-headless, lo que evita
el error `libGL.so.1` que rompía docker / n8n / dokploy.

Algoritmo:
    - Lee la imagen como bump/height map.
    - Sobel horizontal (dx) y vertical (dy), ksize=3, border replicate.
    - normal = normalize(vec3(1/strength, dy, dx))   # orden BGR
    - color = normal * 0.5 + 0.5
    - PNG  -> uint8 * 255
    - EXR  -> float32
    - Nombre de salida: sustituye "bump" (palabra completa) por "normal";
      si no hay "bump", añade "_normal".  ->  <base>_normal.<fmt>

Uso CLI (compatible con bumptonormalmap.py):
    python normalmap.py <ruta_imagen> <strength> <png|exr>
"""

from __future__ import annotations

import os
import re
import sys

import cv2
import numpy as np

NORMAL_FORMAT_CHOICES = ("png", "exr")
DEFAULT_STRENGTH = 2.0
DEFAULT_FORMAT = "png"

# Extensiones que el worker / la cola saben procesar como bump map.
# Todo lo que NO esté acá (fbx, obj, 3ds, blend, glb, gltf, etc.) se salta.
IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".tga",
})


def is_image_name(name: str) -> bool:
    """True si el archivo es una imagen que podemos convertir a normal."""
    ext = os.path.splitext(os.path.basename(name))[1].lower()
    return ext in IMAGE_EXTS


def _normalize(vec: np.ndarray) -> np.ndarray:
    length = np.expand_dims(np.linalg.norm(vec, axis=-1), axis=-1)
    # Evita división por cero en píxeles totalmente planos.
    length[length == 0] = 1.0
    return vec / length


def normal_output_name(input_name: str, output_format: str = DEFAULT_FORMAT) -> str:
    """Devuelve el nombre de archivo de salida para una textura dada.

    Convención: SIN prefijo, sufijo `_normal` (formato original del
    bumptonormalmap). El stem de la textura queda intacto y la normal
    queda como `<root>.<ext>_normal.png` después de que PB le agregue su
    sufijo aleatorio. Sustituye `bump` (palabra completa) por `normal`
    en el root.  Esto se alinea con el formato que ya aceptaba
    Clothfigurator_web y PocketBase no normaliza.

    Ejemplos (antes de que PB agregue su sufijo):
      lauren_fabric_v79t000mki.jpg   -> lauren_fabric_v79t000mki_normal.png
      chair_bump.jpg                 -> chair_normal.png  (bump -> normal)
    """
    no_ext, _ = os.path.splitext(os.path.basename(input_name))
    pattern = r"(?<![a-zA-Z])bump(?![a-zA-Z])"
    new_no_ext = re.sub(pattern, "normal", no_ext, flags=re.IGNORECASE)
    if "normal" not in new_no_ext.lower():
        new_no_ext += "_normal"
    return f"{new_no_ext}.{output_format}"


def is_normal_name(name: str) -> bool:
    """True si el nombre corresponde a una normal ya generada.

    Detecta el flag `_normal` en el stem (cualquier posición). Igual que
    la convención vieja — NO se usa prefijo.
    """
    no_ext, _ = os.path.splitext(os.path.basename(name))
    return "_normal" in no_ext.lower()


def bump_to_normal_bytes(
    data: bytes,
    strength: float = DEFAULT_STRENGTH,
    output_format: str = DEFAULT_FORMAT,
) -> bytes:
    """Convierte los bytes de una imagen a los bytes de su normal map.

    Pensado para el flujo PocketBase: descargas la textura en memoria,
    generas la normal en memoria y la vuelves a subir, sin tocar disco.
    """
    if output_format not in NORMAL_FORMAT_CHOICES:
        raise ValueError(
            f"Formato inválido {output_format!r} (esperado {NORMAL_FORMAT_CHOICES})"
        )
    if strength <= 0.0:
        raise ValueError("strength tiene que ser > 0")

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
    if img is None:
        raise ValueError("No se pudo decodificar la imagen de entrada")

    colors = _compute_normals(img, strength)

    ext = ".png" if output_format == "png" else ".exr"
    if output_format == "png":
        out = np.uint8(colors * 255)
        ok, buf = cv2.imencode(ext, out)
    else:  # exr
        ok, buf = cv2.imencode(ext, colors.astype(np.float32))
    if not ok:
        raise RuntimeError("cv2.imencode falló al codificar la normal")
    return buf.tobytes()


def _compute_normals(img: np.ndarray, strength: float) -> np.ndarray:
    if img.dtype == np.uint8:
        img = img.astype(np.float32) / 255.0
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    ddepth = cv2.CV_64F
    grad_x = cv2.Sobel(img, ddepth, 1, 0, ksize=3, scale=1, delta=0,
                       borderType=cv2.BORDER_REPLICATE)
    grad_y = cv2.Sobel(img, ddepth, 0, 1, ksize=3, scale=1, delta=0,
                       borderType=cv2.BORDER_REPLICATE)

    dx = grad_x[:, :, 0]
    dy = grad_y[:, :, 0]
    inv_strength = np.full_like(dx, 1.0 / strength)
    normals = _normalize(np.stack([inv_strength, dy, dx], axis=-1))  # BGR
    return normals * 0.5 + 0.5


def bump_to_normal_file(
    path: str,
    strength: float = DEFAULT_STRENGTH,
    output_format: str = DEFAULT_FORMAT,
) -> str:
    """Variante en disco (compatibilidad CLI). Devuelve la ruta de salida."""
    with open(path, "rb") as f:
        out_bytes = bump_to_normal_bytes(f.read(), strength, output_format)
    parent = os.path.dirname(os.path.abspath(path))
    out_name = normal_output_name(path, output_format)
    out_path = os.path.join(parent, out_name)
    with open(out_path, "wb") as f:
        f.write(out_bytes)
    print(f'Wrote "{out_path}".')
    return out_path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("uso: python normalmap.py <ruta_imagen> [strength] [png|exr]")
        return 2
    path = argv[1]
    strength = float(argv[2]) if len(argv) > 2 else DEFAULT_STRENGTH
    fmt = argv[3] if len(argv) > 3 else DEFAULT_FORMAT
    bump_to_normal_file(path, strength, fmt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
