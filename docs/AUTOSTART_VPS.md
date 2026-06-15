# Auto-start en VPS (Ubuntu) — FUTURO

> Documentado, **no activado**. Cuando el proyecto esté cerrado, esto deja el
> sistema recibiendo y procesando solo al bootear el servidor.

## 1. Encender el worker automático

En `.env` (o en `docker-compose.yml`) poner el worker en modo automático y
levantar el perfil `auto`:

```bash
# en el VPS, dentro de la carpeta del proyecto
cp .env.example .env   # y rellenar las 4 vars de PocketBase
docker compose up -d --build dashboard
docker compose --profile auto up -d --build worker
```

El `restart: unless-stopped` del compose ya hace que ambos contenedores
revivan si se caen o si reinicia el Docker daemon.

## 2. Que el stack arranque al bootear el server (systemd)

Crear `/etc/systemd/system/donormal.service`:

```ini
[Unit]
Description=donormal stack
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/donormal          # ruta donde clonaste el repo
ExecStart=/usr/bin/docker compose --profile auto up -d --build
ExecStop=/usr/bin/docker compose --profile auto down

[Install]
WantedBy=multi-user.target
```

Activar:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now donormal.service
```

## 3. Flujo final (cuando se integre Unreal)

1. Unreal sube / referencia el **albedo** (ya funcional).
2. El worker detecta la textura sin normal y genera la **normal** (strength 2).
3. Se envía la pareja `{albedoURL, normalURL}` (ver `scripts/unreal_payload.py`,
   hoy comentado) → primero albedo, luego normal, habilitando en el material
   padre `"Base 01 - Normal - Map"` y seteando `"Base 01 - Normal - Texture"`.

## Notas

- El dashboard sigue en `127.0.0.1:8753`. Si lo querés exponer, poné un
  reverse proxy (Caddy/Nginx) con auth delante — NO lo publiques directo.
- Logs persistentes en `./logs/donormal.log` (montado como volumen).
