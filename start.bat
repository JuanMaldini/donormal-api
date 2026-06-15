@echo off
setlocal
cd /d "%~dp0"
title donormal - start (worker nativo)

REM Levanta SOLO el worker automatico (nativo, sin Docker, sin frontend).
REM El worker escanea PocketBase y genera las normales faltantes en bucle.
REM Para el flujo on-demand del dashboard NO hace falta el worker: el
REM dashboard encola y procesa cada item a pedido del usuario.
REM
REM Usar este bat SOLO si queres que se procesen normales en background
REM (ej: la noche, para no esperar en el dashboard).

if not exist ".env" (
  echo [donormal] Falta .env. Copia .env.example a .env y completalo.
  pause & exit /b 1
)

REM ---------- Modo nativo: solo Python, sin Docker ----------
where python >nul 2>&1
if errorlevel 1 ( echo [donormal] Falta Python en PATH. Instalalo desde python.org & pause & exit /b 1 )

if not exist ".venv" (
  echo [donormal] Creando entorno virtual .venv ...
  python -m venv .venv
)
echo [donormal] Instalando dependencias (la primera vez tarda)...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r deploy\requirements.txt
if errorlevel 1 ( echo [donormal] Error instalando dependencias. & pause & exit /b 1 )

echo [donormal] Levantando worker nativo (cmd visible)...
start "donormal-worker" "%~dp0.venv\Scripts\python.exe" scripts\worker.py

echo.
echo [donormal] Worker corriendo. Logs: logs\donormal.log
echo [donormal] Para cerrarlo, ejecuta stop.bat.
endlocal
