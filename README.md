# donormal

Generador de **normal maps** para las texturas de Clothfigurator, conectado a
la **misma** instancia de PocketBase que `Clothfigurator_web` (mismas
colecciones, mismo registro por usuario, mismo array `file[]`).

Replica el algoritmo de tu `Normal_creator` (MircoWerner/BumpToNormalMap):
Sobel → `normalize(1/strength, dy, dx)` → PNG, con **strength=2**. La diferencia
es que acá el algoritmo viene **horneado en el contenedor** y usa
`opencv-python-headless`, así que **no descarga nada en runtime** ni necesita
`libGL.so.1` — que era lo que rompía en n8n / docker / dokploy.

## Cómo se usa

1. `copy .env.example .env` y rellená las 4 variables (sin fallback: si falta
   una, no arranca):
   - `PB_URL`, `PB_TOKEN`, `PB_USERS`, `PB_DATA`.
   - Opcional: `ADMIN_TOKEN` para activar el modo admin del dashboard.
2. **Dashboard** (localhost): doble clic en `startWeb.bat` → abre
   `http://localhost:8753`. Entrás con **tu mismo usuario** del web.

   Los `.bat` se encargan de todo: si **Docker** está corriendo, levantan el
   contenedor; si **no**, caen automáticamente a **modo nativo Python** (crean
   `.venv`, instalan dependencias y arrancan). No necesitás Docker para usar el
   dashboard localmente — solo Python en el PATH.
3. En el panel: a la izquierda **tus texturas**, a la derecha las **normales**
   ya creadas, y abajo los **logs** en vivo. En cada textura sin normal hay un
   botón **Generar normal** (procesamiento on-demand, una a la vez).
4. `stopWeb.bat` para bajar el dashboard.

### Modo admin (opcional)

Si en `.env` ponés `ADMIN_TOKEN=<algo-seguro>`, el dashboard muestra arriba
a la derecha un toggle **“Mis texturas / Todas (admin)”**. La segunda vista
lista los registros de **todos** los usuarios, con sus texturas y normales,
y permite encolar o auto-procesar cualquier registro.

Sin `ADMIN_TOKEN` el modo admin queda oculto.

### Worker automático (fase 2, opcional)

Cuando quieras "todo directo" en vez de elegir una por una:

- `start.bat` levanta el worker que escanea PocketBase y genera todas las
  normales faltantes en bucle. `stop.bat` lo baja.

## Estructura

```
start.bat / stop.bat         worker automático (fase 2)
startWeb.bat / stopWeb.bat   dashboard (localhost)
.env.example                 solo 4 vars de PocketBase
deploy/                      Dockerfile, docker-compose.yml, requirements.txt
scripts/
  normalmap.py   algoritmo Sobel (horneado)
  config.py      carga .env, falla si falta algo
  pocketbase.py  cliente PB (auth, listar, subir/borrar, emparejar)
  worker.py      procesamiento automático (fase 2)
  logs.py        logging a stdout + logs/donormal.log
frontend/
  app.py             FastAPI: login, listar, generar, proxy de imágenes, logs
  static/index.html  dashboard minimalista
logs/                (gitignored)
```

## Notas técnicas

- **Vínculo textura↔normal**: por el flag `_normal` en el nombre (igual que tu
  repo). El emparejado tolera el sufijo aleatorio que PocketBase añade a los
  archivos.
- **Filtro de `relation`**: el dashboard prueba `relation = "user_id"`
  (string) **y** `relation.id ?= "user_id"` (campo relation de PocketBase)
  con fallback automático, igual que `Clothfigurator_web`.
- **Las normales se guardan en el mismo registro** del usuario (`file+`), así el
  web las ve al instante (ya tiene preparado `textureNormalURL`).
- **Solo localhost**: el dashboard se publica en `127.0.0.1:8753`.
- Valores fijos en `config.py` (no en `.env`): formato `png`, puerto `8753`,
  strength `2`, worker apagado.

## Deploy en VPS

Desde la raíz del proyecto (el compose vive en `deploy/`):

```
docker compose -f deploy/docker-compose.yml up -d --build dashboard          # dashboard
docker compose -f deploy/docker-compose.yml --profile auto up -d --build worker   # + worker
```

Ver `docs/AUTOSTART_VPS.md` para arranque automático con systemd.
