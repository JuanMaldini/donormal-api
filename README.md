# Clothfigurator — Normal Worker

Genera el **normal map** de cada textura que se sube al dashboard, y lo deja en
el mismo record de PocketBase, en el campo `file_normal`.

Un proceso. Sin puerto, sin volumen, sin estado. Corre en la VPS dentro de
Dokploy; el estado vive en PocketBase (`normal_status`), no en el contenedor,
así que si se muere arranca otro y sigue donde estaba.

## El ciclo

```
usuario sube una textura
  └─ record en clothfigurator_textures, normal_status="pending"
       └─ el worker lo ve por SSE (~2 s)
            └─ baja el albedo, Sobel en RAM, sube file_normal
                 └─ normal_status="done"
```

La normal **no es un archivo suelto**: es un campo del record de su textura. Por
eso no hay nada que filtrar en el frontend (nunca fue un item de lista), nada
que emparejar por nombre, y nada que se pueda re-convertir: `normal_status`
dice si ya está hecha.

### SSE + polling, las dos cosas

- **SSE** solo: PocketBase no reenvía lo que pasó mientras estabas
  desconectado. Un deploy de 30 s y esas texturas no las procesa nadie.
- **Polling** solo: el usuario espera hasta 5 min mirando "generando".

Los dos productores solo **encolan**. Procesar pasa en un único hilo consumidor:
nunca hay dos normales generándose a la vez.

## Estructura

```
src/
  main.py       gate -> recover -> barrido -> SSE + poll
  pb.py         cliente PocketBase (login de servicio, claim, upload)
  normalmap.py  algoritmo Sobel (horneado, sin descargas en runtime)
  config.py     3 variables de entorno, sin fallbacks
  logs.py       stdout
deploy/
  Dockerfile           lo que buildea Dokploy
  docker-compose.yml   solo para probar en local
  requirements.txt
tools/          scripts de un solo uso. NO entran en la imagen
```

## Configuración

Tres variables. Ninguna es un token.

```
PB_URL=https://pocketbase.vp3dserver.online
PB_WORKER_EMAIL=worker@clothfigurator.local
PB_WORKER_PASSWORD=...
```

PocketBase **no tiene API keys permanentes**: los tokens de impersonate no son
renovables y vencen en silencio. El worker usa una **cuenta de servicio** (un
record de `clothfigurator_users` con `role="worker"`) y auth-with-password; el
cliente reautentica solo cuando el token caduca.

Los nombres de las colecciones **no** son variables: viven en `src/config.py`.
Son fijos en toda instancia, así que como variable solo agregaban otra forma de
configurar mal el worker.

## Deploy (Dokploy)

```
New Application → Provider: GitHub
  Repo:        Clothfigurator-NormalWorker
  Branch:      main
  Build Type:  Dockerfile
  Path:        deploy/Dockerfile
  Environment: PB_URL / PB_WORKER_EMAIL / PB_WORKER_PASSWORD
  Domain:      ninguno
```

No se sube nada a mano. Cada `git push` a `main` es un redeploy. Los logs se ven
en el panel de Dokploy.

## Local

`start.bat` — usa Docker si está corriendo, si no cae a un `.venv` de Python.
`stop.bat` — solo para el modo Docker.

## Requisitos en PocketBase

`clothfigurator_textures` con `file_albedo`, `file_normal`, `normal_status`
(`pending|processing|done|error`), `attempts`, `error_log`, `claimed_at`, y un
índice en `normal_status`. Los file fields **sin `protected`** — es lo que
permite que el visitante del link público y Unreal (que no arrastra sesión)
puedan leer los archivos.
