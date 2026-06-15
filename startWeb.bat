@echo off
setlocal
cd /d "%~dp0"
title dnormal - startWeb

if not exist ".env" (
  echo [dnormal] Falta .env. Copia .env.example a .env y completalo.
  pause & exit /b 1
)

REM ---------- Intento 1: Docker ----------
docker info >nul 2>&1
if %errorlevel%==0 (
  echo [dnormal] Docker detectado. Levantando dashboard en contenedor...
  docker compose -f deploy\docker-compose.yml up -d --build dashboard
  if errorlevel 1 ( echo [dnormal] Error al levantar en Docker. & pause & exit /b 1 )
  goto open
)

REM ---------- Fallback: Python nativo ----------
echo [dnormal] Docker no esta corriendo. Usando modo nativo (Python)...
where python >nul 2>&1
if errorlevel 1 ( echo [dnormal] Falta Python en PATH. Instalalo desde python.org & pause & exit /b 1 )

if not exist ".venv" (
  echo [dnormal] Creando entorno virtual .venv ...
  python -m venv .venv
)
echo [dnormal] Instalando dependencias (la primera vez tarda)...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r deploy\requirements.txt
if errorlevel 1 ( echo [dnormal] Error instalando dependencias. & pause & exit /b 1 )

echo [dnormal] Levantando dashboard nativo...
start "dnormal-dashboard" /min "%~dp0.venv\Scripts\python.exe" -m uvicorn app:app --app-dir frontend --host 127.0.0.1 --port 8753

:open
timeout /t 3 >nul
echo [dnormal] Dashboard en http://localhost:8753
start "" http://localhost:8753
endlocal
