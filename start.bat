@echo off
setlocal
cd /d "%~dp0"
title donormal - start (worker)

REM Worker automatico (FASE 2): escanea PocketBase y genera las normales
REM faltantes en bucle. Para el flujo on-demand del dashboard NO hace falta.

if not exist ".env" (
  echo [donormal] Falta .env. Copia .env.example a .env y completalo.
  pause & exit /b 1
)

REM ---------- Intento 1: Docker ----------
docker info >nul 2>&1
if %errorlevel%==0 (
  echo [donormal] Docker detectado. Worker en contenedor...
  docker compose -f deploy\docker-compose.yml --profile auto up -d --build worker
  if errorlevel 1 ( echo [donormal] Error al levantar el worker. & pause & exit /b 1 )
  goto done
)

REM ---------- Fallback: Python nativo ----------
echo [donormal] Docker no esta corriendo. Usando modo nativo (Python)...
where python >nul 2>&1
if errorlevel 1 ( echo [donormal] Falta Python en PATH. Instalalo desde python.org & pause & exit /b 1 )

if not exist ".venv" (
  echo [donormal] Creando entorno virtual .venv ...
  python -m venv .venv
)
echo [donormal] Instalando dependencias (la primera vez tarda)...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r deploy\requirements.txt
if errorlevel 1 ( echo [donormal] Error instalando dependencias. & pause & exit /b 1 )

echo [donormal] Levantando worker nativo...
set WORKER_ENABLED=true
start "donormal-worker" /min "%~dp0.venv\Scripts\python.exe" scripts\worker.py

:done
echo [donormal] Worker corriendo. Logs: logs\donormal.log
endlocal
