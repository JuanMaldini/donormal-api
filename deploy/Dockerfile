# Worker de normales. Un proceso: sin puerto, sin volumen, sin estado.
#
# Claves anti-dolor:
#   - opencv-python-headless: no necesita libGL.so.1, que era lo que rompia
#     en n8n / docker / dokploy.
#   - dependencias horneadas en build-time: cero pip install en runtime.
#
# Contexto de build = raiz del proyecto.
# `tools/` NO se copia a proposito: son scripts de un solo uso que hablan con
# la PocketBase vieja, y no hay motivo para darle al worker credenciales de
# dos bases.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY deploy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# PYTHONUNBUFFERED: sin esto los logs quedan en el buffer de Python y el panel
# de Dokploy no muestra nada hasta que el proceso muere.
ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/src/main.py"]
