@echo off
setlocal
cd /d "%~dp0"
title donormal - startWeb (frontend nativo)

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

echo [donormal] Levantando dashboard nativo (cmd visible)...
start "donormal-dashboard" "%~dp0.venv\Scripts\python.exe" -m uvicorn app:app --app-dir frontend --host 127.0.0.1 --port 8753

REM Espera a que el server responda /api/health (max 30s)
set /a TRIES=0
:wait_loop
set /a TRIES+=1
if %TRIES% GTR 30 (
  echo [donormal] El server no respondio en 30s. Revisa la ventana "donormal-dashboard" para ver el error.
  pause & exit /b 1
)
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri 'http://127.0.0.1:8753/api/health').StatusCode } catch { 0 }" > "%TEMP%\donormal_health.txt" 2>nul
set /p HEALTH=<"%TEMP%\donormal_health.txt"
if "%HEALTH%"=="200" goto health_ok
timeout /t 1 >nul
goto wait_loop

:health_ok
del "%TEMP%\donormal_health.txt" 2>nul
echo [donormal] Dashboard en http://localhost:8753  (abro el navegador...)
start "" http://localhost:8753
echo.
echo [donormal] Listo. El server corre en la ventana "donormal-dashboard".
echo [donormal] Cuando quieras cerrarlo, ejecuta stopWeb.bat.
endlocal
