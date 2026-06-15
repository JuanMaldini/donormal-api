@echo off
cd /d "%~dp0"
echo [donormal] Deteniendo dashboard...
REM Docker (si estaba)
docker info >nul 2>&1 && docker compose -f deploy\docker-compose.yml stop dashboard >nul 2>&1
REM Nativo (si estaba)
taskkill /FI "WINDOWTITLE eq donormal-dashboard*" /T /F >nul 2>&1
echo [donormal] Listo.
