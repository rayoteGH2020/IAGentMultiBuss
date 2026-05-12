# Paso 02 — Servicios locales con Docker Compose

## Objetivo

Levantar en local los servicios de los que depende la app durante el desarrollo: **PostgreSQL con pgvector**, **Redis**, y **Langfuse self-hosted**. Crear un `docker-compose.yml` reproducible que cualquier máquina pueda arrancar con un comando.

Al final del paso, `docker compose up -d` levanta los tres servicios. Las URLs de conexión (`DATABASE_URL`, `REDIS_URL`, etc.) las consumirá la app vía **variables de entorno** inyectadas con **Infisical** (`infisical run -- ...`); no se usa fichero `.env` (ver `Agents.md` §2).

## Pre-requisitos

- Paso 01 completado.
- **Infisical** CLI instalado y acceso al proyecto/entorno donde guardarás secretos de desarrollo (ver `Agents.md` §2). Las keys de Langfuse de este paso se guardan ahí, no en `.env`.
- Docker y Docker Compose instalados (Docker Desktop o Docker Engine + plugin compose).
- Puertos libres en local: `5432` (Postgres), `6379` (Redis), `3000` (Langfuse), `5433` (Postgres de Langfuse).

## Contexto relevante

- `arquitectura.md` sección 12 (Despliegue) — entendemos que Langfuse va self-hosted.
- `Agents.md` §1 (stack) y **§2 (Infisical, sin `.env`)**.

## Tareas

- [x] Crear `docker/docker-compose.yml` con servicios `postgres`, `redis`, `langfuse-db`, `langfuse`.
- [x] Crear `docker/postgres/init.sql` para activar extensiones (`pgvector`, `pgcrypto`, `uuid-ossp`).
- [x] Crear directorio `docker/data/` (gitignored) para volúmenes.
- [x] Añadir `docker/data/` a `.gitignore`.
- [x] Levantar los servicios y verificar que están sanos.
- [x] Probar conexión a Postgres con `psql` y confirmar que `pgvector` está activo.
- [x] Probar conexión a Redis con `redis-cli ping`. >>>RAR<<< Va dentro de Docker
- [x] Abrir Langfuse en `http://localhost:3000` y crear cuenta admin local.
- [x] Tras crear las API keys en Langfuse (Settings → API Keys), **registrarlas en Infisical** (entorno `dev` o el que uses): `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (p. ej. `http://localhost:3000`). No crear `.env`.
- [x] Crear `scripts/dev_up.sh` y `scripts/dev_down.sh`.
- [ ] Commit: `chore: docker compose with postgres, redis, langfuse`.

## Detalles técnicos

### `docker/docker-compose.yml`

```yaml
name: mi-saas-dev

services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: saas-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: saas
      POSTGRES_PASSWORD: saas
      POSTGRES_DB: saas
    ports:
      - "5432:5432"
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U saas -d saas"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: saas-redis
    restart: unless-stopped
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"
    volumes:
      - ./data/redis:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  langfuse-db:
    image: postgres:16-alpine
    container_name: saas-langfuse-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: langfuse
      POSTGRES_DB: langfuse
    ports:
      - "5433:5432"
    volumes:
      - ./data/langfuse-db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U langfuse"]
      interval: 5s
      timeout: 5s
      retries: 5

  langfuse:
    image: langfuse/langfuse:2
    container_name: saas-langfuse
    restart: unless-stopped
    depends_on:
      langfuse-db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://langfuse:langfuse@langfuse-db:5432/langfuse
      NEXTAUTH_URL: http://localhost:3000
      NEXTAUTH_SECRET: ${LANGFUSE_NEXTAUTH_SECRET:-changeme-in-dev}
      SALT: ${LANGFUSE_SALT:-changeme-in-dev}
      TELEMETRY_ENABLED: "false"
      LANGFUSE_INIT_USER_EMAIL: dev@local
      LANGFUSE_INIT_USER_PASSWORD: changeme123
      LANGFUSE_INIT_USER_NAME: Dev
      LANGFUSE_INIT_ORG_NAME: Dev
      LANGFUSE_INIT_PROJECT_NAME: mi-saas-dev
    ports:
      - "3000:3000"
```

### `docker/postgres/init.sql`

```sql
-- Extensiones necesarias para la BD principal
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Esto se ejecuta solo la primera vez que se crea el volumen.
-- Si necesitas activar extensiones después, hazlo con una migración Alembic.
```

### `scripts/dev_up.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Levantando servicios..."
docker compose -f docker/docker-compose.yml up -d

echo "Esperando a Postgres..."
until docker compose -f docker/docker-compose.yml exec -T postgres pg_isready -U saas > /dev/null 2>&1; do
  sleep 1
done

echo "Esperando a Redis..."
until docker compose -f docker/docker-compose.yml exec -T redis redis-cli ping > /dev/null 2>&1; do
  sleep 1
done

echo "Esperando a Langfuse..."
until curl -fsS http://localhost:3000/api/public/health > /dev/null 2>&1; do
  sleep 1
done

echo ""
echo "✓ Postgres   → localhost:5432 (user=saas pass=saas db=saas)"
echo "✓ Redis      → localhost:6379"
echo "✓ Langfuse   → http://localhost:3000 (dev@local / changeme123)"
echo ""
echo "Recuerda registrar las API keys de Langfuse en Infisical (LANGFUSE_*); ver Agents.md §2."
```

### `scripts/dev_down.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose -f docker/docker-compose.yml down
echo "Servicios detenidos. Datos persistidos en docker/data/."
```

Hacer ejecutables:

```bash
chmod +x scripts/dev_up.sh scripts/dev_down.sh
```

### Añadir al `.gitignore`

```gitignore
# Datos locales de Docker
docker/data/
```

## Criterios de aceptación

- [ ] `./scripts/dev_up.sh` levanta los 3 servicios sin errores.
- [ ] `docker compose -f docker/docker-compose.yml ps` muestra los 4 contenedores con estado `Up (healthy)`.
- [ ] `psql postgresql://saas:saas@localhost:5432/saas -c "SELECT extname FROM pg_extension"` muestra al menos `vector`, `pgcrypto`, `uuid-ossp`.
- [ ] `redis-cli -h localhost ping` responde `PONG`.
- [ ] `http://localhost:3000` carga Langfuse y se puede hacer login con `dev@local` / `changeme123`.
- [ ] En Langfuse: creada una API key (Settings → API Keys) y **guardadas en Infisical** (mismos nombres que usará `pydantic-settings`):
      - `LANGFUSE_PUBLIC_KEY` = `pk-lf-...`
      - `LANGFUSE_SECRET_KEY` = `sk-lf-...`
      - `LANGFUSE_HOST` = `http://localhost:3000`
- [ ] `./scripts/dev_down.sh` detiene los contenedores limpiamente.
- [ ] `docker/data/` está en `.gitignore`.
- [ ] Commit hecho.

## Comandos útiles

```bash
# Levantar
./scripts/dev_up.sh

# Ver logs en vivo
docker compose -f docker/docker-compose.yml logs -f
docker compose -f docker/docker-compose.yml logs -f postgres

# Entrar al Postgres
docker compose -f docker/docker-compose.yml exec postgres psql -U saas -d saas
# o desde el host (si tienes psql)
psql postgresql://saas:saas@localhost:5432/saas

# Verificar extensiones
psql postgresql://saas:saas@localhost:5432/saas -c "\dx"

# Borrar todo (datos incluidos) y empezar de cero
docker compose -f docker/docker-compose.yml down -v
rm -rf docker/data
./scripts/dev_up.sh

# Detener servicios pero conservar datos
./scripts/dev_down.sh
```

## Lo que NO toca este paso

- Conectar la app a estos servicios: lo hace el Paso 03.
- Crear las tablas: las migraciones se crean en el Paso 06.
- Configurar Langfuse para producción: por ahora solo dev local.
- Docker Compose para producción: en el paso de despliegue (post-15).

## Posibles problemas

**Puerto 5432 ocupado**: tienes otro Postgres corriendo. Cambia el mapeo a `"5433:5432"` en `docker-compose.yml` y actualiza en **Infisical** el secreto `DATABASE_URL` (o el que definas en Paso 03) para que apunte al host `localhost` y al **puerto publicado** correcto (p. ej. 5433).

**`pgvector/pgvector:pg16` tarda en descargar**: la imagen pesa ~150MB. Primera vez puede tardar.

**Langfuse no arranca**: revisa logs con `docker compose ... logs langfuse`. Suele ser que `langfuse-db` no terminó de inicializar. Reinicia el servicio: `docker compose ... restart langfuse`.

**En Windows / WSL2**: si los volúmenes en `docker/data/` dan problemas de permisos, mueve los volúmenes a volúmenes nombrados de Docker (`volumes: pg_data:` en lugar de bind mount).

**Health check de Postgres falla intermitente**: aumenta el `retries: 10`.

## Siguiente paso

`Paso03.md` — Crear la app FastAPI mínima con configuración Pydantic (`BaseSettings`, `env_file=None`), healthchecks que verifican Postgres y Redis, y logging estructurado con structlog. Arranque local con **`infisical run -- ...`** para inyectar secretos (ver `Agents.md` §2).
