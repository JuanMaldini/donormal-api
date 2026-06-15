@echo off
setlocal
cd /d "%~dp0"
echo [donormal] Deteniendo dashboard...

REM Docker (best-effort, no rompe si no esta)
docker info >nul 2>&1 && docker compose -f deploy\docker-compose.yml stop dashboard >nul 2>&1

REM Nativo: matar lo que escucha en el puerto 8753. Usamos PowerShell inline
REM y volcamos los PIDs a un archivo temporal para evitar problemas de
REM expansion y comillas en el .bat.

set "TMPF=%TEMP%\donormal_pids.txt"

set FOUND=0

REM 1) PIDs en puerto 8753 (con estado Listen)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort 8753 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Write-Output $_ }" > "%TMPF%" 2>nul
for /f "usebackq delims=" %%P in ("%TMPF%") do (
    if not "%%P"=="" (
        echo [donormal] Matando PID %%P ^(ocupa puerto 8753^)...
        taskkill /PID %%P /T /F >nul 2>&1
        set FOUND=1
    )
)

REM 2) Backup: uvicorn por argumentos
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app:app' -and $_.CommandLine -match '8753' } | Select-Object -ExpandProperty ProcessId | ForEach-Object { Write-Output $_ }" > "%TMPF%" 2>nul
for /f "usebackq delims=" %%P in ("%TMPF%") do (
    if not "%%P"=="" (
        echo [donormal] Matando PID %%P ^(uvicorn app:app^)...
        taskkill /PID %%P /T /F >nul 2>&1
        set FOUND=1
    )
)

REM 3) Backup: ventana con titulo donormal-dashboard
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'donormal-dashboard*' } | Select-Object -ExpandProperty Id | ForEach-Object { Write-Output $_ }" > "%TMPF%" 2>nul
for /f "usebackq delims=" %%P in ("%TMPF%") do (
    if not "%%P"=="" (
        echo [donormal] Matando PID %%P ^(ventana donormal-dashboard^)...
        taskkill /PID %%P /T /F >nul 2>&1
        set FOUND=1
    )
)

if "%FOUND%"=="0" (
    echo [donormal] No se encontro ningun proceso del dashboard. Ya estaba detenido?
) else (
    echo [donormal] Listo.
)

del /q "%TMPF%" 2>nul
endlocal
