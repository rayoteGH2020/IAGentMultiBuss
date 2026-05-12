# TODO — Paso 02 (Docker local) y entorno Windows

Checklist derivada de la guía para completar **Paso02.md** en tu máquina (PowerShell / Windows). Secretos: **Infisical**, sin `.env` (ver `Agents.md` §2).

---

## 1. Docker instalado y en el PATH

- [x] Instalar **Docker Desktop** (o Engine + plugin Compose) si no está instalado: https://www.docker.com/products/docker-desktop/
- [x] Activar Docker Desktop y esperar a que esté en ejecución.
- [x] Cerrar y reabrir la terminal para refrescar el PATH.
- [x] Comprobar:

```powershell
docker --version
docker compose version
```

---

## 2. Puertos libres

Puertos esperados por `docker/docker-compose.yml`: **5432** (Postgres), **6379** (Redis), **3000** (Langfuse), **5433** (Postgres Langfuse).

- [x] Comprobar que no estén ocupados:

```powershell
netstat -ano | findstr ":5432 :6379 :3000 :5433"
```

- [ ] Si **5432** está ocupado: cambiar el mapeo en `docker-compose.yml` (p. ej. `"5433:5432"` para la app principal) y documentar en Infisical el `DATABASE_URL` con el puerto correcto (ver `Paso02.md` §Posibles problemas).

---

## 3. Ficheros del Paso 02 presentes

- [x] Existe `docker/docker-compose.yml`
- [x] Existe `docker/postgres/init.sql`
- [x] Existen `scripts/dev_up.sh` y `scripts/dev_down.sh`
- [x] `docker/data/` está en `.gitignore` (datos locales, no commitear)

---

## 4. Levantar servicios (Windows)

En NTFS **`chmod +x` no aplica** como en Linux; elige una opción:

### Opción A — Git Bash o WSL

- [ ] `chmod +x scripts/dev_up.sh scripts/dev_down.sh`
- [ ] `./scripts/dev_up.sh`

### Opción B — PowerShell (sin scripts `.sh`)

- [x] Desde la raíz del repo:

```powershell
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs -f langfuse
```

---

## 5. Criterios de salud (Paso02)

- [x] `./scripts/dev_up.sh` **o** `docker compose ... up -d` sin errores
- [x] `docker compose -f docker/docker-compose.yml ps` muestra los **4** contenedores `Up (healthy)` (`saas-postgres`, `saas-redis`, `saas-langfuse-db`, `saas-langfuse`)
- [x] Extensiones en Postgres principal (`vector`, `pgcrypto`, `uuid-ossp`):

```powershell
docker compose -f docker/docker-compose.yml exec postgres psql -U saas -d saas -c "SELECT extname FROM pg_extension"
```

- [x] Redis responde `PONG`:

```powershell
docker compose -f docker/docker-compose.yml exec redis redis-cli ping
```

- [x] Abrir `http://localhost:3000` — Langfuse carga
- [x] Login con `dev@local.dev` / `changeme123`

---

## 6. Langfuse → API keys → Infisical

- [x] En Langfuse: **Settings → API Keys** → crear claves
- [x] Registrar en Infisical (entorno `dev` o el que uses), **sin** crear `.env`:

| Variable | Valor típico |
|----------|----------------|
| `LANGFUSE_PUBLIC_KEY` | `pk-lf-...` |
| `LANGFUSE_SECRET_KEY` | `sk-lf-...` |
| `LANGFUSE_HOST` | `http://localhost:3000` |

- [x] Verificar con el CLI de Infisical (`infisical login`, `infisical secrets set` / `get`) según tu flujo

---

## 7. Comandos útiles (referencia rápida)

```powershell
# Logs en vivo
docker compose -f docker/docker-compose.yml logs -f
docker compose -f docker/docker-compose.yml logs -f postgres

# psql desde el host (si tienes psql instalado)
psql postgresql://saas:saas@localhost:5432/saas -c "\dx"  # pragma: allowlist secret

# Borrar todo y empezar de cero (datos incluidos)
docker compose -f docker/docker-compose.yml down -v
Remove-Item -Recurse -Force docker/data   # PowerShell; en bash: rm -rf docker/data
# Luego volver a levantar (§4)
```

---

## 8. Cerrar servicios (conservar datos en `docker/data/`)

- [ ] PowerShell:

```powershell
docker compose -f docker/docker-compose.yml down
```

- [ ] O desde Git Bash/WSL: `./scripts/dev_down.sh`

---

## 9. Cierre del paso en Git

- [ ] Marcar criterios de aceptación en `Paso02.md` cuando todo esté verificado
- [ ] Commit según el paso (Conventional Commits, inglés):

```text
chore: docker compose with postgres, redis, langfuse
```

- [ ] Preferible rama `paso/02-docker-compose` y PR a `main` con CI verde (`Agents.md` §10)

---

## 10. Mejora opcional (Windows)

- [ ] Añadir `scripts/dev_up.ps1` y `scripts/dev_down.ps1` equivalentes a los `.sh` para un solo comando en PowerShell sin Git Bash

---

## Siguiente paso en la spec

- [ ] **Paso03.md** — App FastAPI mínima, `BaseSettings` con `env_file=None`, healthchecks Postgres/Redis, structlog, arranque con `infisical run -- ...`
