@echo off
setlocal
cd /d "%~dp0"
title normal-worker - stop

REM Solo aplica al modo Docker. En modo nativo el worker corre en primer plano:
REM se para con Ctrl+C en su propia ventana.

docker compose -f deploy\docker-compose.yml down
echo [worker] Contenedor bajado.
endlocal
