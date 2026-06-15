# Imagen única para dashboard y worker.
# Clave anti-dolor: opencv-python-headless (no necesita libGL) y deps horneadas
# en build-time (cero descargas / pip-install en runtime, lo que rompía n8n/dokploy).
# Contexto de build = raíz del proyecto (ver deploy/docker-compose.yml).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY deploy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/
COPY frontend/ ./frontend/

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/scripts \
    LOG_DIR=/app/logs

RUN mkdir -p /app/logs
EXPOSE 8753

CMD ["uvicorn", "app:app", "--app-dir", "/app/frontend", "--host", "0.0.0.0", "--port", "8753"]
