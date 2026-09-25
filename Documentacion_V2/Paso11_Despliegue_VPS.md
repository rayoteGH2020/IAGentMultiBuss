# Paso11 - Despliegue en VPS (Docker Compose + Caddy)

Estado: **artefactos en repo y probados en local** (2026-09-24). Falta ops: VPS, dominio, Infisical `prod`, Clerk prod.
Decision: D013 (Compose + Caddy, no Coolify).

Objetivo: poner la app en Internet para usuarios invitados, con secretos solo en Infisical, TLS automatico, backups cifrados fuera de la VPS y rollback conocido.

## 0. Que hay en el repo

| Fichero | Rol |
| --- | --- |
| `Dockerfile` | Imagen unica API + worker. Tailwind v3.4.17 con checksum fijado, usuario no root, sin secretos. |
| `.dockerignore` | Excluye `.env*`, `.infisical.json`, `.git`, datos locales y tests. |
| `deploy/docker-compose.prod.yml` | `caddy`, `api` (2 workers uvicorn), `worker` (ARQ), `postgres` (pgvector), `redis`. Solo Caddy publica 80/443. |
| `deploy/Caddyfile` | TLS Let's Encrypt, proxy a `api:8000`, limite 20 MB, `/metrics` no expuesto. |
| `deploy/postgres/02-app-role.sh` | Crea `saas_app` (NOBYPASSRLS) con la password de Infisical antes de migrar. |
| `deploy/healthcheck.py` | Healthcheck de la API con Host publico y `X-Forwarded-Proto: https`. |
| `deploy/scripts/deploy.sh` | checkout del tag → build → backup → `alembic upgrade head` → arranque. |
| `deploy/scripts/backup.sh` | `pg_dump -Fc` → gpg AES256 → disco local (7 dias) + R2. |
| `deploy/scripts/restore.sh` | Restore destructivo con `--yes-destroy-data`. |
| `tests/unit/test_deploy_config.py` | Invariantes: variables de Settings cubiertas, puertos, sin `env_file`, no root, etc. |

Validado en local (2026-09-24): `docker build` OK; stack con `APP_ENV=production` sano (api/worker/postgres/redis healthy); migraciones desde BD vacia hasta `p67`; `/docs` 404; HTTP → 307 a HTTPS; Host ajeno → 400; backup cifrado → borrado → restore OK; `caddy validate` OK.

## 1. Modelo de roles Postgres (importante)

- `POSTGRES_USER` (p. ej. `saas_owner`): superusuario del contenedor. **Solo** migraciones y backups.
- `saas_app`: rol de la app y del worker. `NOSUPERUSER NOBYPASSRLS` → RLS se aplica.
- `DATABASE_URL` (Infisical) apunta a `saas_app`. `MIGRATIONS_DATABASE_URL` apunta al propietario.

> **Riesgo detectado:** en `dev` la app conecta como `saas` (superusuario), asi que RLS **nunca** se ha ejercitado desde la UI. Los tests de integracion si usan `saas_app`. Antes de abrir a usuarios, cambiar `DATABASE_URL` de Infisical `dev` a `saas_app` y repetir el smoke manual (login, documentos, chat, SADM).
>
> ```powershell
> # dev local: la password de saas_app en dev es 'saas' (p06_saas_app_03)
> # Infisical dev: DATABASE_URL=postgresql+asyncpg://saas_app:<password>@localhost:5432/saas
> infisical run --env=dev -- uv run python -c "import os; from urllib.parse import urlparse; print(urlparse(os.environ['DATABASE_URL']).username)"
> ```
> Debe imprimir `saas_app`.

## 2. VPS y dominio

- [ ] VPS Linux (p. ej. Hetzner, 4 vCPU / 8 GB, Ubuntu 24.04 LTS), region UE.
- [ ] Dominio con registro `A` (y `AAAA` si hay IPv6) del subdominio de la app → IP de la VPS.
- [ ] Clerk prod pedira ademas sus CNAME (§5).

Endurecimiento minimo (como root, primera vez):

```bash
adduser deploy && usermod -aG sudo deploy
# Copiar tu clave publica a /home/deploy/.ssh/authorized_keys, y despues:
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
apt-get update && apt-get -y upgrade && apt-get -y install unattended-upgrades ufw git gpg
ufw default deny incoming && ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443 && ufw enable
```

Comprobar: `ufw status` muestra solo 22, 80, 443. Recuerda que los puertos que publica Docker se saltan ufw: por eso el compose solo publica los de Caddy.

Docker (repositorio oficial) e Infisical CLI:

```bash
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy
curl -1sLf 'https://artifacts-cli.infisical.com/setup.deb.sh' | bash && apt-get install -y infisical
docker compose version && infisical --version
```

## 3. Infisical: Machine Identity de la VPS

1. Infisical → Organization → Machine Identities → crear `vps-prod` con **Universal Auth**.
2. Darle acceso al proyecto con rol de **solo lectura** y solo al entorno `prod`.
3. Opcional recomendado: restringir *Client Secret Trusted IPs* a la IP de la VPS.
4. En la VPS crear el unico fichero de secreto fuera de Infisical:

```bash
sudo install -d -m 700 /etc/iagent
sudo install -m 600 /dev/null /etc/iagent/infisical-identity.conf
sudo nano /etc/iagent/infisical-identity.conf
```

Contenido (sin comillas):

```text
INFISICAL_UNIVERSAL_AUTH_CLIENT_ID=<client id>
INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET=<client secret>
INFISICAL_PROJECT_ID=<project id>
INFISICAL_ENV=prod
INFISICAL_API_URL=https://eu.infisical.com/api
```

`INFISICAL_API_URL` solo si tu cuenta es EU Cloud o self-hosted. Los scripts rechazan el fichero si no tiene permisos 600.

## 4. Variables de Infisical `prod`

Todas las de `PasosParaProduccion.md` §3, mas estas propias del despliegue:

| Variable | Valor |
| --- | --- |
| `APP_DOMAIN` | `app.tudominio.com` (sin `https://`) |
| `ACME_EMAIL` | email para avisos de certificados |
| `APP_BASE_URL` | `https://app.tudominio.com` |
| `SECURITY_ALLOWED_HOSTS` | `app.tudominio.com` |
| `POSTGRES_USER` / `POSTGRES_DB` | p. ej. `saas_owner` / `saas` |
| `POSTGRES_PASSWORD` | aleatoria, 32+ caracteres |
| `SAAS_APP_DB_PASSWORD` | aleatoria, distinta de la anterior |
| `DATABASE_URL` | `postgresql+asyncpg://saas_app:<SAAS_APP_DB_PASSWORD>@postgres:5432/saas` |
| `MIGRATIONS_DATABASE_URL` | `postgresql+asyncpg://saas_owner:<POSTGRES_PASSWORD>@postgres:5432/saas` |
| `REDIS_PASSWORD` | aleatoria |
| `REDIS_URL` | `redis://:<REDIS_PASSWORD>@redis:6379/0` |
| `LLM_RETRY_TRANSIENT_ERRORS` | `true` |
| `BACKUP_ENCRYPTION_PASSPHRASE` | aleatoria; **copiala tambien a tu gestor de contrasenas** |
| `BACKUP_R2_BUCKET` | bucket R2 solo para backups (p. ej. `iagent-backups`) |
| `BACKUP_R2_ACCESS_KEY_ID` / `BACKUP_R2_SECRET_ACCESS_KEY` | token R2 limitado a ese bucket |

Generar valores aleatorios (en tu PC, pegarlos directamente en Infisical):

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # ENCRYPTION_KEY
```

Las passwords de `DATABASE_URL`/`REDIS_URL` van dentro de una URL: usa `token_urlsafe` (sin `@`, `/`, `:`).
Se puede aplazar Langfuse: con `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` vacias el tracing se desactiva y `llm_calls` sigue registrando coste.

R2: crear los buckets de prod (`R2_BUCKET`) y de backups, **sin acceso publico**. En el de backups, regla de lifecycle: borrar objetos de `postgres/` a los 30 dias.

## 5. Clerk produccion

- [ ] Crear instancia **Production** y completar los CNAME DNS que pide Clerk.
- [ ] Restrictions → Sign-up mode = **Restricted** (solo por invitacion).
- [ ] Organizations → desactivar que los usuarios creen organizaciones.
- [ ] Webhook `https://app.tudominio.com/api/webhooks/clerk` con `organizationMembership.created|updated|deleted` (+ `user.created`, `organization.created` si se usan). Signing secret → `CLERK_WEBHOOK_SECRET`.
- [ ] `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL` de la instancia prod.
- [ ] `CLERK_JWT_AZP_ALLOWLIST=https://app.tudominio.com` (inferencia: confirmar con un JWT real tras el primer login, `Paso01` §3).
- [ ] Crear la org SADM → `ADMIN_CLERK_ORG_ID`. Tu user id → `SUPERADMIN_CLERK_USER_IDS`.

Alta de un cliente: crear su org en Clerk → invitar al usuario → login → asignar plan en `/sadm/plans`.

## 6. Primer despliegue

Desde tu PC: CI verde en `main` y tag.

```powershell
git tag v0.1.0
git push origin v0.1.0
```

En la VPS (usuario `deploy`):

```bash
sudo install -d -o deploy -g deploy /opt/iagent
git clone <url-del-repo> /opt/iagent
cd /opt/iagent
sudo bash deploy/scripts/deploy.sh v0.1.0
```

Si el repo es privado: clave de despliegue de solo lectura (GitHub → Settings → Deploy keys).

Comprobar:

```bash
curl -sI https://app.tudominio.com/health          # 200 y certificado valido
curl -s  https://app.tudominio.com/health/db       # {"status":"ok"}
curl -s  https://app.tudominio.com/health/redis    # {"status":"ok"}
curl -sI https://app.tudominio.com/docs            # 404
curl -sI http://app.tudominio.com/                 # 308 a https
docker compose -f deploy/docker-compose.prod.yml ps    # todo healthy
tail -n 5 /var/backups/iagent/releases.log
```

Despues: smoke manual de `PasosParaProduccion.md` §7 y firma Go/No-Go en `Paso10`.

## 7. Backups

Cron diario 03:30 UTC (como root):

```bash
echo '30 3 * * * root bash /opt/iagent/deploy/scripts/backup.sh >> /var/log/iagent-backup.log 2>&1' | sudo tee /etc/cron.d/iagent-backup
sudo bash /opt/iagent/deploy/scripts/backup.sh     # primera ejecucion manual
ls -la /var/backups/iagent/
```

- [ ] Backup visible en el bucket R2 `postgres/`.
- [ ] **Restore probado** al menos una vez en una VPS o instancia vacia (no en prod):

```bash
sudo bash deploy/scripts/restore.sh /var/backups/iagent/saas-<fecha>.dump.gpg --yes-destroy-data
```

## 8. Actualizar y volver atras

```bash
sudo bash deploy/scripts/deploy.sh v0.1.1                 # nueva version (con backup + migraciones)
sudo bash deploy/scripts/deploy.sh v0.1.0 --no-migrate    # rollback de codigo si el esquema es compatible
```

Si la version nueva migro el esquema de forma incompatible: `restore.sh` con el backup que `deploy.sh` hizo justo antes. No usar `alembic downgrade` en prod.

Kill-switch de coste sin redeploy: `ENTITLEMENTS_DISABLED_FEATURES` en Infisical y reiniciar:

```bash
cd /opt/iagent && sudo bash -c 'source deploy/scripts/_lib.sh; load_identity; with_secrets docker compose up -d --force-recreate api worker'
```

Parada dura del gasto LLM: `docker compose -f deploy/docker-compose.prod.yml stop worker`.

## 9. Logs

```bash
docker compose -f deploy/docker-compose.prod.yml logs -f --tail=100 api worker
```

Rotacion: json-file 10 MB x 5 por contenedor.

## Tests

```powershell
infisical run -- uv run pytest tests/unit/test_deploy_config.py -q
```

## Criterios de aceptacion

- [x] Imagen construye y arranca con `APP_ENV=production` (local, 2026-09-24).
- [x] App conecta como `saas_app` (NOBYPASSRLS); migraciones con rol propietario.
- [x] Backup cifrado y restore verificados (local).
- [ ] `DATABASE_URL` de `dev` cambiado a `saas_app` y smoke manual OK.
- [ ] VPS endurecida, Infisical `prod` completo, Clerk prod restringido.
- [ ] Primer deploy con TLS valido y healthchecks OK.
- [ ] Backup en R2 y restore probado fuera de prod.
