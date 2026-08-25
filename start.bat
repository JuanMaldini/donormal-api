@echo off
setlocal
cd /d "%~dp0"
title normal-worker (local)

REM Corre el worker en TU maquina, contra la PocketBase que diga el .env.
REM En la VPS no se usa esto: alla lo levanta Dokploy desde deploy/Dockerfile.

if not exist ".env" (
  echo [worker] Falta .env. Copia .env.example a .env y completalo.
  pause & exit /b 1
)

REM ---------- Docker si esta corriendo ----------
docker info >nul 2>&1
if %errorlevel%==0 (
  echo [worker] Docker detectado. Levantando contenedor...
  docker compose -f deploy\docker-compose.yml up -d --build
  if errorlevel 1 ( echo [worker] Error al levantar. & pause & exit /b 1 )
  echo [worker] Corriendo. Logs: docker logs -f normal-worker
  goto :eof
)

REM ---------- Fallback: Python nativo ----------
echo [worker] Docker no esta corriendo. Modo nativo...
where python >nul 2>&1
if errorlevel 1 ( echo [worker] Falta Python en PATH. & pause & exit /b 1 )

if not exist ".venv" (
  echo [worker] Creando .venv ...
  python -m venv .venv
)
echo [worker] Instalando dependencias (la primera vez tarda)...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r deploy\requirements.txt
if errorlevel 1 ( echo [worker] Error instalando dependencias. & pause & exit /b 1 )

echo [worker] Arrancando. Ctrl+C para parar.
".venv\Scripts\python.exe" src\main.py

endlocal
