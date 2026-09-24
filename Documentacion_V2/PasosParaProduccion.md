# PasosParaProduccion

Fecha: 2026-08-05 · Actualizado: 2026-09-24
Estado: checklist operativa go-live **en orden de ejecucion**. Codigo de producto cerrado (Pasos 02–07, 09); artefactos de despliegue en repo y probados en local (D013, `Paso11`). Pendiente: ops (cuentas, VPS, Infisical `prod`, Clerk prod, QA manual, firma Go/No-Go).
Fuente: consolidado de `Documentacion_V2` (`Paso00`–`Paso11`, `Seguridad_V2`, `SADM_V2`, `Arquitectura_V2`, `Planes_Entitlements`, `Backlog_Priorizado`, `Decision_Log`) y `docs/environment-variables.md`.

Como usar este fichero: ir fase a fase, de arriba abajo. No empezar una fase si la anterior tiene casillas abiertas sin aceptacion explicita. Marcar cada casilla solo con evidencia (comando, captura, fecha). Detalle ampliado del despliegue: `Paso11_Despliegue_VPS.md`.

---

## Lista de tareas para arrancar el paso a produccion

Resumen ordenado. Cada linea remite a su fase.

| # | Tarea | Donde | Quien | Fase |
| --- | --- | --- | --- | --- |
| 1 | Decidir alcance del primer go-live (recomendado: soft launch, solo invitados, sin Stripe/WA/TG/Calendar) | — | Tu | 0 |
| 2 | Commitear cambios pendientes (`p66` renombrada, `p67`, tests, ficheros de despliegue) y dejar CI verde en `main` | PC / GitHub | Tu (+ asistente) | 1 |
| 3 | Cambiar `DATABASE_URL` de Infisical `dev` a `saas_app` y repetir smoke manual (RLS real) | Infisical dev | Tu | 1 |
| 4 | Comprar/elegir **dominio** (p. ej. `app.tudominio.com`) | Registrador DNS | Tu | 2 |
| 5 | Contratar **VPS** (Hetzner u otro UE, 4 vCPU / 8 GB, Ubuntu 24.04) | Proveedor VPS | Tu | 2 |
| 6 | Crear **buckets R2** de prod y de backups + tokens R2 separados | Cloudflare | Tu | 2 |
| 7 | Crear **API keys LLM de prod** (Google, Anthropic, Voyage) con limite de gasto | Consolas proveedores | Tu | 2 |
| 8 | **Rotar secretos historicos** expuestos en docs antiguos | Todos los proveedores | Tu | 2 |
| 9 | Endurecer VPS (usuario `deploy`, SSH con clave, ufw) e instalar Docker + Infisical CLI | VPS | Tu | 3 |
| 10 | Crear **Machine Identity** de Infisical para la VPS y su fichero `/etc/iagent/infisical-identity.conf` | Infisical + VPS | Tu | 4 |
| 11 | Rellenar **Infisical `prod`** con secretos nuevos (no copiar `dev`) | Infisical | Tu | 5 |
| 12 | Crear **instancia Clerk Production**: DNS, registro restringido, sin creacion de orgs, webhook, org SADM | Clerk + DNS | Tu | 6 |
| 13 | Apuntar DNS del dominio a la VPS | Registrador DNS | Tu | 7 |
| 14 | Crear **tag** y ejecutar `deploy.sh` (primer despliegue) | PC + VPS | Tu | 8 |
| 15 | Verificar healthchecks, TLS, login y confirmar `azp` del JWT | Navegador + VPS | Tu | 8 |
| 16 | Programar **backups** (cron) y **probar un restore** fuera de prod | VPS | Tu | 9 |
| 17 | Dar de alta el primer cliente piloto (org Clerk → invitacion → plan en `/sadm/plans`) | Clerk + SADM | Tu | 10 |
| 18 | **QA manual** en produccion | Navegador | Tu | 11 |
| 19 | Verificacion de **seguridad operativa** | VPS + app | Tu | 12 |
| 20 | **Firma Go/No-Go** y documento de release | `Paso10` | Tu | 13 |
| 21 | Vigilar logs y coste las primeras 48–72 h | VPS + SADM | Tu | 14 |

---

## Fase 0. Reglas y alcance

Reglas que no se negocian:

- Fuente de verdad: `Documentacion_V2/`. Si un doc antiguo contradice V2, gana V2.
- Secretos solo en Infisical. Nunca `.env` ni `env_file`, ni valores reales en Markdown, chats o issues.
- No abrir produccion con `WEBHOOK_ALLOW_UNSIGNED=true` ni `LANGFUSE_CAPTURE_CONTENT=true` (Settings no arranca si estan a `true` fuera de development).
- Despliegue: monolito (API + worker ARQ) en una VPS con Docker Compose + Caddy (D013). Sin Kubernetes, microservicios ni Coolify.
- Identidades solo via Clerk Dashboard (D005). La app no crea usuarios ni organizaciones.

Alcance del primer go-live:

| Alcance | Implicacion |
| --- | --- |
| **Soft launch / invitados (recomendado)** | Documentos + knowledge + chat. Planes asignados a mano en `/sadm/plans`. Sin Stripe, WhatsApp, Telegram, Google Calendar ni voz. Langfuse aplazable. |
| Produccion comercial | Todo lo anterior + Stripe operativo (Price IDs, webhook, claves) + canales activos con QA real. |

- [ ] Alcance decidido y anotado aqui: ______________________

---

## Fase 1. Preparar el codigo (en tu PC)

### 1.1 Commitear y CI verde

Estado a 2026-09-24: hay cambios sin commitear (renombrado `p66_drop_analytics_ent_01`, nueva `p67_plans_basic_adv_prem_01`, tests, `Dockerfile`, `deploy/`, docs).

Los tests usan la BD `saas_test`, nunca `saas` (crearla/migrarla una vez y tras cada migracion nueva):

```powershell
git status --short
infisical run -- bash scripts/test_db_setup.sh
infisical run -- uv run ruff check app tests
infisical run -- uv run ruff format --check app tests
infisical run -- uv run mypy app
infisical run -- uv run pytest tests/unit -q
infisical run -- uv run pytest tests/integration -q -m "integration and not real_llm"
infisical run -- uv run pytest tests/unit/test_deploy_config.py tests/unit/test_migration_revisions.py -q
infisical run -- uv run alembic heads
```

- [ ] `alembic heads` = un unico head `p67_plans_basic_adv_prem_01`.
- [ ] Commits separados (planes / despliegue), PR a `main`, CI verde, merge.

### 1.2 RLS real en dev (riesgo detectado 2026-09-24)

En `dev` la app conecta como `saas` (superusuario) y **RLS no se aplica** en la UI. En prod conectara como `saas_app` (NOBYPASSRLS). Hay que probar ese modo antes.

1. En Infisical `dev`: `DATABASE_URL=postgresql+asyncpg://saas_app:<password-dev>@localhost:5432/saas` (`<password-dev>` = la que crea `p06_saas_app_03` en dev). Las migraciones en dev siguen necesitando el superusuario: ejecutalas con `DATABASE_URL` del superusuario solo para ese comando.
2. Comprobar el rol:

```powershell
infisical run --env=dev -- uv run python -c "import os; from urllib.parse import urlparse; print(urlparse(os.environ['DATABASE_URL']).username)"
```

3. Arrancar API + worker y recorrer: login, subir documento, chat con citas, knowledge, `/sadm` (dashboard, plans, usage, documentos rechazados).

- [ ] Imprime `saas_app`.
- [ ] Smoke OK sin errores de permisos en logs (`permission denied`, `row-level security`).

### 1.3 Secretos en repo

```powershell
infisical run -- uv run detect-secrets scan --baseline .secrets.baseline
git grep -n "TOKEN\|PASSWORD\|SECRET\|API_KEY\|Bearer" -- ':!*.lock'
```

- [x] Sin secretos reales en docs versionados (`7bc1aea`); `Documentacion/` fuera del repo (`eae1c00`).
- [ ] Repetido en el commit que se va a desplegar.

### 1.4 Controles ya cerrados en codigo (no requieren accion, solo conocerlos)

- [x] Catalogo de planes `basic`/`advanced`/`premium` (D012, `p67`), gates y cuotas (Paso02–04).
- [x] Kill-switch: `ENTITLEMENTS_DISABLED_FEATURES` + parar worker.
- [x] Webhooks: limite de body + dedupe anti-replay (Clerk/WA/TG/Stripe).
- [x] `media_limits` antes de OCR/LLM.
- [x] Sync de memberships Clerk (created/updated/deleted).
- [x] Allowlist `azp`/`aud` obligatoria en staging/production.
- [x] `/docs`, `/redoc`, `/openapi.json` desactivados con `APP_ENV=production`.
- [x] Artefactos de despliegue probados en local (build, stack `production`, migraciones desde cero, backup/restore cifrado).

---

## Fase 2. Contratar servicios y preparar cuentas

### 2.1 Dominio

- [ ] Dominio registrado. Subdominio de la app decidido: `app.<dominio>` → sera `APP_DOMAIN`.
- [ ] Acceso al panel DNS (hara falta para la VPS y para los CNAME de Clerk).

### 2.2 VPS

- [ ] Proveedor UE (RGPD), p. ej. Hetzner Cloud CX32/CPX31 o similar: 4 vCPU, 8 GB RAM, 80+ GB disco, Ubuntu 24.04 LTS.
- [ ] Alta con tu clave SSH publica (no password).
- [ ] Backups/snapshots del proveedor activados (capa extra, no sustituye a `backup.sh`).
- [ ] IP publica anotada.

### 2.3 Cloudflare R2

1. R2 → Create bucket `iagent-prod` (nombre libre) → sera `R2_BUCKET`. Sin dominio publico ni acceso publico.
2. R2 → Create bucket `iagent-backups`. Sin acceso publico. Settings → Object lifecycle rules → borrar objetos con prefijo `postgres/` a los 30 dias.
3. R2 → Manage API tokens:
   - Token A "app prod": *Object Read & Write* solo sobre `iagent-prod` → `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`.
   - Token B "backups": *Object Read & Write* solo sobre `iagent-backups` → `BACKUP_R2_ACCESS_KEY_ID` / `BACKUP_R2_SECRET_ACCESS_KEY`.
4. Anotar el Account ID → `R2_ACCOUNT_ID`.

- [ ] Dos buckets privados, dos tokens con alcance limitado.

### 2.4 Claves LLM de produccion

- [ ] `GOOGLE_API_KEY` nueva (proyecto Google distinto de dev si es posible), con presupuesto/alerta de facturacion.
- [ ] `ANTHROPIC_API_KEY` nueva (workspace prod) con limite de gasto mensual.
- [ ] `VOYAGE_API_KEY` nueva.

Motivo: si una clave de dev se filtra no afecta a prod, y el limite del proveedor es un segundo freno de coste ademas de las cuotas de plan.

### 2.5 Rotacion de secretos historicos (Backlog P0-1)

- [ ] Invalidar en cada proveedor (Clerk, Google, Anthropic, Voyage, R2, Langfuse, WhatsApp/Telegram, Postgres/Redis de entornos compartidos) cualquier credencial que haya aparecido en documentacion antigua. Borrar el texto no basta.

---

## Fase 3. Preparar la VPS

Como `root` la primera vez:

```bash
adduser deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh && cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
```

Comprobar desde tu PC que `ssh deploy@<IP>` funciona **antes** de cerrar root:

```bash
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
apt-get update && apt-get -y upgrade
apt-get -y install unattended-upgrades ufw git gpg
ufw default deny incoming && ufw default allow outgoing
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443
ufw enable
```

Docker e Infisical CLI:

```bash
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy
curl -1sLf 'https://artifacts-cli.infisical.com/setup.deb.sh' | bash
apt-get install -y infisical
docker compose version && infisical --version
```

Codigo en la VPS (como `deploy`). Repo privado: GitHub → repo → Settings → Deploy keys → anadir clave publica de la VPS **solo lectura**.

```bash
ssh-keygen -t ed25519 -C "vps-deploy" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub      # pegar en Deploy keys
sudo install -d -o deploy -g deploy /opt/iagent
git clone git@github.com:<usuario>/<repo>.git /opt/iagent
```

- [ ] `ssh root@<IP>` rechazado; `ssh deploy@<IP>` OK.
- [ ] `sudo ufw status` → solo 22, 80, 443.
- [ ] `/opt/iagent` clonado.

Nota: los puertos que publica Docker se saltan ufw. Por eso `deploy/docker-compose.prod.yml` solo publica los de Caddy (test `test_only_caddy_publishes_ports`).

---

## Fase 4. Infisical: Machine Identity de la VPS

1. Infisical → Organization → Access Control → Identities → Create `vps-prod`, metodo **Universal Auth**.
2. Universal Auth → crear Client Secret. Opcional recomendado: *Client Secret Trusted IPs* = IP de la VPS.
3. Proyecto → Access Control → Machine Identities → anadir `vps-prod` con rol de **solo lectura** (Viewer) limitado al entorno `prod`.
4. Comprobar que el slug de entorno es `prod` (el slug `production` no existe en tu proyecto).
5. En la VPS:

```bash
sudo install -d -m 700 /etc/iagent
sudo install -m 600 /dev/null /etc/iagent/infisical-identity.conf
sudo nano /etc/iagent/infisical-identity.conf
```

```text
INFISICAL_UNIVERSAL_AUTH_CLIENT_ID=<client id>
INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET=<client secret>
INFISICAL_PROJECT_ID=<project id>
INFISICAL_ENV=prod
INFISICAL_API_URL=https://eu.infisical.com/api
```

`INFISICAL_API_URL` solo si tu cuenta es EU Cloud o self-hosted; si usas `app.infisical.com`, omite la linea. Es el **unico** secreto fuera de Infisical (D013); los scripts se niegan a usarlo si no tiene permisos 600.

- [ ] `sudo stat -c '%a %U' /etc/iagent/infisical-identity.conf` → `600 root`.

---

## Fase 5. Infisical entorno `prod` (variables)

Valores **nuevos**, nunca copiados de `dev`. Generar aleatorios en tu PC y pegarlos directamente en Infisical:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"                                  # passwords, APP_SECRET_KEY
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"      # ENCRYPTION_KEY
```

Usa `token_urlsafe` para passwords que van dentro de una URL (no contiene `@`, `/` ni `:`).

### 5.1 App y seguridad HTTP

| Variable | Valor |
| --- | --- |
| `APP_ENV` | `production` |
| `APP_SECRET_KEY` | aleatorio (rotarlo invalida sesiones) |
| `APP_BASE_URL` | `https://app.<dominio>` |
| `APP_DOMAIN` | `app.<dominio>` (sin `https://`; lo usa Caddy y el healthcheck) |
| `ACME_EMAIL` | tu email (avisos de certificados Let's Encrypt) |
| `LOG_LEVEL` | `INFO` |
| `SECURITY_ALLOWED_HOSTS` | `app.<dominio>` (sin `*`) |
| `SECURITY_HTTPS_REDIRECT` | `true` |
| `SECURITY_HSTS_ENABLED` | `true` |
| `WEBHOOK_ALLOW_UNSIGNED` | `false` |
| `ENCRYPTION_KEY` | Fernet nueva (no la de dev/CI) |

### 5.2 Postgres y Redis (contenedores de la VPS)

| Variable | Valor |
| --- | --- |
| `POSTGRES_USER` | `saas_owner` (propietario; solo migraciones y backups) |
| `POSTGRES_PASSWORD` | aleatorio |
| `POSTGRES_DB` | `saas` |
| `SAAS_APP_DB_PASSWORD` | aleatorio, distinto del anterior |
| `DATABASE_URL` | `postgresql+asyncpg://saas_app:<SAAS_APP_DB_PASSWORD>@postgres:5432/saas` |
| `MIGRATIONS_DATABASE_URL` | `postgresql+asyncpg://saas_owner:<POSTGRES_PASSWORD>@postgres:5432/saas` |
| `REDIS_PASSWORD` | aleatorio |
| `REDIS_URL` | `redis://:<REDIS_PASSWORD>@redis:6379/0` |

`postgres` y `redis` son los nombres de servicio del compose (red interna Docker), no hosts publicos.

### 5.3 Storage R2

| Variable | Valor |
| --- | --- |
| `R2_ACCOUNT_ID` | Account ID de Cloudflare |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | token A (Fase 2.3) |
| `R2_BUCKET` | `iagent-prod` |
| `R2_REGION` | `auto` |
| `R2_ENDPOINT_URL` | **no definir** (vacio = R2 real, no MinIO) |
| `R2_PUBLIC_URL` | no definir salvo necesidad explicita |

### 5.4 Backups

| Variable | Valor |
| --- | --- |
| `BACKUP_ENCRYPTION_PASSPHRASE` | aleatorio. **Guardar copia en tu gestor de contrasenas**: sin ella los backups no se pueden abrir |
| `BACKUP_R2_BUCKET` | `iagent-backups` |
| `BACKUP_R2_ACCESS_KEY_ID` / `BACKUP_R2_SECRET_ACCESS_KEY` | token B (Fase 2.3) |

### 5.5 Clerk (se rellenan en la Fase 6)

`CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL`, `CLERK_WEBHOOK_SECRET`, `CLERK_JWT_AZP_ALLOWLIST`, `ADMIN_CLERK_ORG_ID`, `SUPERADMIN_CLERK_USER_IDS`.

### 5.6 LLM y observabilidad

| Variable | Valor |
| --- | --- |
| `GOOGLE_API_KEY` / `ANTHROPIC_API_KEY` / `VOYAGE_API_KEY` | claves de prod (Fase 2.4) |
| `LLM_RETRY_TRANSIENT_ERRORS` | `true` |
| `LLM_MODEL_*` | no definir salvo override medido |
| `LANGFUSE_CAPTURE_CONTENT` | `false` |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | **vacias en soft launch** (tracing desactivado; `llm_calls` sigue registrando coste). Rellenar cuando exista Langfuse prod |

### 5.7 Limites (dejar defaults salvo decision)

`DOCUMENT_MAX_IMAGE_EDGE_PX=20000`, `DOCUMENT_MAX_IMAGE_PIXELS=40000000`, `KNOWLEDGE_MAX_FILE_SIZE_BYTES=15728640`. No usar los valores bajos de las pruebas manuales de Paso01.

### 5.8 Opcionales segun alcance (vacias en soft launch)

- SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`/`SMTP_SSL`) y `EMAIL_SADM` para avisos de usuarios sin org.
- Stripe: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY` (vacio = checkout/portal deshabilitados).
- WhatsApp: `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`.
- Google Calendar: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` (redirect URI de prod en Google Cloud).
- `METRICS_TOKEN` (Caddy bloquea `/metrics` desde Internet igualmente).

- [ ] Variables 5.1–5.4 y 5.6 rellenadas.
- [ ] Ningun valor copiado de `dev`.

---

## Fase 6. Clerk produccion

1. Clerk Dashboard → tu aplicacion → crear instancia **Production**. Dominio: `<dominio>`.
2. Clerk → Domains: crear en tu DNS los **CNAME** que indica (frontend API `clerk.<dominio>`, cuentas, email). Esperar a que Clerk los marque verificados.
3. Configure → Restrictions → **Sign-up mode = Restricted** (solo por invitacion).
4. Configure → Organizations → activar Organizations y **desactivar** que los usuarios creen organizaciones.
5. Configure → Paths / redirect URLs → `https://app.<dominio>`.
6. Webhooks → Add endpoint `https://app.<dominio>/api/webhooks/clerk`, eventos:
   - `organizationMembership.created`, `organizationMembership.updated`, `organizationMembership.deleted`
   - `user.created` y `organization.created` si los usas.
   - Signing secret → `CLERK_WEBHOOK_SECRET` en Infisical `prod`.
7. API Keys → `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY` (de **Production**, `sk_live_`/`pk_live_`). `CLERK_JWKS_URL` = `https://clerk.<dominio>/.well-known/jwks.json` (copiar el que muestra Clerk).
8. `CLERK_JWT_AZP_ALLOWLIST=https://app.<dominio>` (inferido; sin una allowlist la app **no arranca** en production). Se confirma con un JWT real en la Fase 8.
9. Crear la organizacion **SADM**, anadirte como admin → su id (`org_...`) en `ADMIN_CLERK_ORG_ID`; tu user id (`user_...`) en `SUPERADMIN_CLERK_USER_IDS`.

- [ ] Instancia Production con DNS verificado.
- [ ] Registro restringido y creacion de orgs desactivada.
- [ ] Webhook creado (las entregas fallaran hasta la Fase 8; es normal).
- [ ] Variables Clerk en Infisical `prod`.

---

## Fase 7. DNS de la app

En el registrador: registro `A` `app` → IP de la VPS (y `AAAA` si la VPS tiene IPv6). Caddy necesita que resuelva **antes** del primer arranque para obtener el certificado.

```bash
dig +short app.<dominio>      # debe devolver la IP de la VPS
```

- [ ] Resuelve a la VPS.

---

## Fase 8. Primer despliegue

### 8.1 Tag (en tu PC, con CI verde en `main`)

```powershell
git checkout main
git pull
git tag v0.1.0
git push origin v0.1.0
```

### 8.2 Deploy (en la VPS)

```bash
cd /opt/iagent
sudo bash deploy/scripts/deploy.sh v0.1.0
```

Que hace `deploy.sh` (en este orden): comprueba que el checkout no tiene cambios locales → `git checkout` del tag → `docker compose build` → arranca Postgres y Redis (en el primer arranque, `docker/postgres/init.sql` crea extensiones y `deploy/postgres/02-app-role.sh` crea `saas_app` con `SAAS_APP_DB_PASSWORD`) → `backup.sh` → `alembic upgrade head` con `MIGRATIONS_DATABASE_URL` → arranca API, worker y Caddy esperando healthchecks → anota el release en `/var/backups/iagent/releases.log`.

Nota: `backup.sh` necesita las variables de la Fase 5.4; sin ellas el primer deploy se detiene antes de migrar.

### 8.3 Verificacion

```bash
curl -sI https://app.<dominio>/health            # 200 y certificado valido
curl -s  https://app.<dominio>/health/db         # {"status":"ok"}
curl -s  https://app.<dominio>/health/redis      # {"status":"ok"}
curl -sI https://app.<dominio>/docs              # 404
curl -sI http://app.<dominio>/                   # 308 hacia https
curl -sI https://app.<dominio>/metrics/module1   # 404 (bloqueado en Caddy)
docker compose -f /opt/iagent/deploy/docker-compose.prod.yml ps
docker compose -f /opt/iagent/deploy/docker-compose.prod.yml exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"select rolname, rolsuper, rolbypassrls from pg_roles where rolname='saas_app'\""
tail -n 3 /var/backups/iagent/releases.log
```

- [ ] Los 5 servicios `healthy`/`running`.
- [ ] `saas_app|f|f` (sin superusuario, sin bypass RLS).
- [ ] `alembic current` (lo imprime `deploy.sh`) = `p67_plans_basic_adv_prem_01 (head)`.

### 8.4 Login y `azp`

1. Entrar en `https://app.<dominio>` con tu usuario de la org SADM.
2. Si entra: la allowlist `azp` es correcta. Si vuelve a `/login` o da 401: DevTools → Cookies → `__session` → decodificar el payload (jwt.io o local) y copiar **solo** el valor de `azp` a `CLERK_JWT_AZP_ALLOWLIST`; reiniciar:

```bash
cd /opt/iagent && sudo bash -c 'source deploy/scripts/_lib.sh; load_identity; with_secrets docker compose up -d --force-recreate api worker'
```

3. Clerk → Webhooks → Deliveries: provocar un evento (cambiar tu rol y volver) → entrega **2xx**. Hacer *Retry* del mismo delivery → sigue 2xx y en logs aparece `webhook.dedupe_replay`:

```bash
docker compose -f /opt/iagent/deploy/docker-compose.prod.yml logs api | grep -E "webhook\.(dedupe_replay|invalid_signature)"
```

- [ ] Login OK en `/` y en `/sadm`.
- [ ] Webhook Clerk 2xx y retry = no-op.

---

## Fase 9. Backups

```bash
echo '30 3 * * * root bash /opt/iagent/deploy/scripts/backup.sh >> /var/log/iagent-backup.log 2>&1' | sudo tee /etc/cron.d/iagent-backup
sudo bash /opt/iagent/deploy/scripts/backup.sh
sudo ls -la /var/backups/iagent/
```

- [ ] Fichero `saas-<fecha>.dump.gpg` en local y en R2 `iagent-backups/postgres/`.
- [ ] Cron instalado (`cat /etc/cron.d/iagent-backup`).
- [ ] **Restore probado** en una VPS/instancia desechable con el mismo repo e identidad (nunca sobre prod para probar):

```bash
sudo bash deploy/scripts/restore.sh /var/backups/iagent/saas-<fecha>.dump.gpg --yes-destroy-data
```

---

## Fase 10. Primer cliente piloto

Flujo unico (D005):

1. Clerk Dashboard → Organizations → Create → nombre del cliente.
2. Invitar al usuario a esa org (rol `admin` para el responsable; `member` para el resto).
3. El usuario acepta la invitacion y hace login → la app crea tenant y membership locales.
4. Tu, en `https://app.<dominio>/sadm/plans` → asignar plan (`basic` / `advanced` / `premium`).
5. Comprobar en `/sadm` que aparece la org y su uso.

Usuario que entra sin org: ve `/onboarding` y puede avisar al SuperAdmin; no puede crear organizaciones.

- [ ] Primer tenant piloto con plan asignado.

---

## Fase 11. QA manual en produccion

Detalle: `Paso10_QA_Release_Produccion.md`, `Paso07`.

### 11.1 Auth y tenant

- [ ] Login admin tenant.
- [ ] Login member (menu: Chat/Citas; sin Documentos/Ajustes).
- [ ] Tenant A no ve datos de Tenant B (dos orgs de prueba, mismo documento buscado desde ambas).
- [ ] Mutacion sin token CSRF bloqueada.
- [ ] Quitar usuario de la org en Clerk → siguiente request a `/login`.
- [ ] Bajar admin → member → pierde rutas admin.
- [ ] Member de la org SADM no entra a `/sadm`.

### 11.2 Documentos

- [ ] Subir factura y ticket; el worker los procesa.
- [ ] Documento invalido falla con mensaje claro; retry / dismiss.
- [ ] Multi-IVA visible.
- [ ] En R2 `iagent-prod` los objetos no son publicos (URL directa sin firma → acceso denegado).

### 11.3 Knowledge y chat

- [ ] Subir knowledge; indexacion termina.
- [ ] Chat con citas; hide thread.
- [ ] Si Langfuse activo: solo metadatos.

### 11.4 Planes

- [ ] Sidebar segun plan; URL directa a feature no incluida → denegada.
- [ ] Cuota bloquea antes de gastar LLM.
- [ ] Cambio de plan en `/sadm/plans` se refleja.

### 11.5 Canales, calendario y billing (solo si entran en el alcance)

- [ ] Google OAuth con callback de prod; voz → evento.
- [ ] WhatsApp / Telegram con firma real y replay = no-op (`Paso01` §4).
- [ ] Stripe checkout/portal con Price IDs de los tres planes.

---

## Fase 12. Seguridad operativa (verificacion)

- [ ] JWT validado contra JWKS + `azp`.
- [ ] App con `saas_app`; RLS FORCE en tablas tenant.
- [ ] CSRF en mutaciones web.
- [ ] Webhooks firmados, body limitado, dedupe.
- [ ] Tokens OAuth/API cifrados con `ENCRYPTION_KEY`.
- [ ] Audit log en acciones sobre datos de cliente y acciones SADM.
- [ ] SADM no muestra secretos ni contenido de Langfuse; sin provision de usuarios/orgs.
- [ ] Solo 22/80/443 abiertos: desde tu PC `nmap -Pn <IP>` (o equivalente).
- [ ] Sin secretos nuevos en el commit desplegado.

```powershell
infisical run -- uv run pytest tests/unit/test_llm_observability.py tests/unit/test_clerk_jwt_audience.py tests/unit/test_superadmin_permissions.py tests/unit/test_deploy_config.py -q
```

---

## Fase 13. Go / No-Go y documento de release

- [ ] Gaps abiertos revisados (seguridad, coste, producto).
- [ ] Riesgos aceptados por escrito (p. ej. Langfuse aplazado, CSP con `unsafe-inline`/`unsafe-eval` por Alpine, canales sin QA real).
- [ ] Firma en `Paso10_QA_Release_Produccion.md`.

```text
Release: YYYY-MM-DD HH:MM Europe/Madrid
Tag / commit: v0.1.0 / <sha>
Alembic: p67_plans_basic_adv_prem_01
Infisical: prod
Gaps aceptados: <lista o "ninguno">
Smoke: OK | parcial
Responsable: <nombre>
```

---

## Fase 14. Operacion

### 14.1 Primeras 48–72 h

- [ ] Revisar logs 1–2 h tras abrir.
- [ ] Coste: `/sadm` (usage, chat usage), `llm_calls` y consolas de los proveedores LLM.

```bash
docker compose -f /opt/iagent/deploy/docker-compose.prod.yml logs -f --tail=100 api worker
```

### 14.2 Nuevas versiones

```bash
sudo bash /opt/iagent/deploy/scripts/deploy.sh v0.1.1
```

Antes: CI verde, migraciones revisadas a mano, tag. `deploy.sh` siempre hace backup antes de migrar.

### 14.3 Rollback

- Codigo sin cambio de esquema incompatible: `sudo bash deploy/scripts/deploy.sh <tag-anterior> --no-migrate`.
- Esquema incompatible: `restore.sh` con el backup que hizo `deploy.sh` justo antes (`/var/backups/iagent/`). **No** usar `alembic downgrade` en prod (`p64` hace `DROP TABLE plans`).

### 14.4 Kill-switch de coste

- Por feature, sin redeploy: `ENTITLEMENTS_DISABLED_FEATURES` en Infisical + recrear api/worker (comando de 8.4).
- Parada dura: `docker compose -f /opt/iagent/deploy/docker-compose.prod.yml stop worker` y, si hace falta, rotar las API keys LLM.

### 14.5 Mantenimiento

- [ ] Actualizaciones de seguridad del SO automaticas (`unattended-upgrades`).
- [ ] Revisar mensualmente imagenes base (`python:3.12-slim-bookworm`, `pgvector/pgvector:pg16`, `redis:7-alpine`, `caddy:2.10-alpine`).
- [ ] Contacto/canal de incidencias definido.

---

## Aplazado (fuera del soft launch)

| Tema | Que hace falta |
| --- | --- |
| Langfuse prod | Instancia self-hosted (web, worker, ClickHouse, Redis, S3) o decision alternativa en `Decision_Log`; claves en Infisical |
| Stripe | Price IDs de `basic`/`advanced`/`premium` en `plans.stripe_price_id`, webhook `/api/webhooks/stripe`, claves (Paso09) |
| WhatsApp / Telegram | Credenciales, webhook a URL prod, QA real + replay |
| Google Calendar / voz | OAuth client de prod con redirect URI; no se publicita (D012) |
| CSP estricta | Migrar a `@alpinejs/csp` y quitar `unsafe-inline`/`unsafe-eval` (Paso01 §6) |
| Staging | Mismo compose en otra VPS con Infisical `staging` para ensayar releases |

---

## Ficheros generados para el despliegue (referencia)

| Fichero | Rol |
| --- | --- |
| `Dockerfile` | Imagen unica API + worker; Tailwind v3.4.17 con checksum; usuario no root; sin secretos |
| `.dockerignore` | Excluye `.env*`, `.infisical.json`, `.git`, datos locales, tests |
| `.gitattributes` | LF obligatorio en scripts y ficheros de despliegue |
| `deploy/docker-compose.prod.yml` | `caddy`, `api`, `worker`, `postgres`, `redis`; solo Caddy publica puertos |
| `deploy/Caddyfile` | TLS automatico, proxy a `api:8000`, limite 20 MB, `/metrics` bloqueado |
| `deploy/postgres/02-app-role.sh` | Crea `saas_app` (NOBYPASSRLS) con password de Infisical |
| `deploy/healthcheck.py` | Healthcheck de la API con Host publico y `X-Forwarded-Proto` |
| `deploy/scripts/_lib.sh` | Carga la Machine Identity y ejecuta con secretos de Infisical |
| `deploy/scripts/deploy.sh` | Deploy / rollback de codigo |
| `deploy/scripts/backup.sh` | Backup cifrado a disco y R2 |
| `deploy/scripts/restore.sh` | Restore destructivo con confirmacion |
| `tests/unit/test_deploy_config.py` | Invariantes de despliegue |
| `Documentacion_V2/Paso11_Despliegue_VPS.md` | Detalle del despliegue |

## Referencias

| Tema | Documento |
| --- | --- |
| Reglas / secretos | `Documentacion_V2/AGENTS.md` |
| Arquitectura y deploy | `Documentacion_V2/Arquitectura_V2.md` §16 |
| Decisiones | `Documentacion_V2/Decision_Log.md` (D005, D011, D012, D013) |
| Despliegue VPS | `Documentacion_V2/Paso11_Despliegue_VPS.md` |
| Seguridad | `Documentacion_V2/Seguridad_V2.md`, `Paso01_Seguridad_Residual.md` |
| SADM | `Documentacion_V2/SADM_V2.md` |
| Planes | `Documentacion_V2/Planes_Entitlements.md` |
| QA release | `Documentacion_V2/Paso10_QA_Release_Produccion.md` |
| Variables | `docs/environment-variables.md` |

Si un paso de este fichero contradice un `PasoXX` mas reciente, actualizar este documento o el paso y dejar constancia en `Decision_Log.md`.
