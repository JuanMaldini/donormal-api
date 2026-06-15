@echo off
cd /d "%~dp0"
echo [dnormal] Deteniendo worker...
REM Docker (si estaba)
docker info >nul 2>&1 && docker compose -f deploy\docker-compose.yml --profile auto stop worker >nul 2>&1
REM Nativo (si estaba)
taskkill /FI "WINDOWTITLE eq dnormal-worker*" /T /F >nul 2>&1
echo [dnormal] Listo.
