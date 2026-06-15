@echo off
setlocal
cd /d "%~dp0"
echo [donormal] Deteniendo worker...

REM Docker (best-effort, no rompe si no esta)
docker info >nul 2>&1 && docker compose -f deploy\docker-compose.yml --profile auto stop worker >nul 2>&1

REM Nativo: matar python que corre scripts\worker.py.
set "TMPF=%TEMP%\donormal_pids.txt"

set FOUND=0

REM 1) python que ejecuta scripts\worker.py
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'scripts[\\/]worker\.py' } | Select-Object -ExpandProperty ProcessId | ForEach-Object { Write-Output $_ }" > "%TMPF%" 2>nul
for /f "usebackq delims=" %%P in ("%TMPF%") do (
    if not "%%P"=="" (
        echo [donormal] Matando PID %%P ^(scripts\worker.py^)...
        taskkill /PID %%P /T /F >nul 2>&1
        set FOUND=1
    )
)

REM 2) Backup: ventana con titulo donormal-worker
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'donormal-worker*' } | Select-Object -ExpandProperty Id | ForEach-Object { Write-Output $_ }" > "%TMPF%" 2>nul
for /f "usebackq delims=" %%P in ("%TMPF%") do (
    if not "%%P"=="" (
        echo [donormal] Matando PID %%P ^(ventana donormal-worker^)...
        taskkill /PID %%P /T /F >nul 2>&1
        set FOUND=1
    )
)

if "%FOUND%"=="0" (
    echo [donormal] No se encontro ningun proceso del worker. Ya estaba detenido?
) else (
    echo [donormal] Listo.
)

del /q "%TMPF%" 2>nul
endlocal
