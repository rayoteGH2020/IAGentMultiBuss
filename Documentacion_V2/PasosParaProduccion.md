# PasosParaProduccion

Fecha: 2026-08-05 · Actualizado: 2026-10-01
Estado: checklist operativa go-live **en orden de ejecucion** y **unica fuente** de tareas del paso a produccion (los `PasoXX` remiten aqui). Codigo base en repo (Pasos 02–07; Stripe retirado, D016); artefactos de despliegue en repo y probados en local (D013, `Paso11`). Pendiente: cierre del codigo del producto minimo (tabla "Cierre del producto minimo" de `Backlog_Priorizado.md`, filas 5-8; la 0, la 1, la 1b, la 2, la 3 y la 4 ya estan hechas y la 9 son las Fases 2-13 de este fichero) y ops (cuentas, VPS, Infisical `prod`, Clerk prod, QA manual, firma Go/No-Go).
Fuente: consolidado de `Documentacion_V2` (`Paso00`–`Paso11`, `Seguridad_V2`, `SADM_V2`, `Arquitectura_V2`, `Planes_Entitlements`, `Backlog_Priorizado`, `Decision_Log`) y `docs/environment-variables.md`.

Como usar este fichero: ir fase a fase, de arriba abajo, con una excepcion: las Fases 2-7 no dependen del codigo y se hacen en paralelo a la Fase 1 y al cierre del codigo (Backlog, filas 1-8; orden completo en "Orden de ejecucion" del Backlog). Desde la Fase 8, no empezar una fase si alguna anterior (incluida la Fase 1) tiene casillas abiertas sin aceptacion explicita; justo antes de la Fase 8, repasar la Fase 5 por si los bloques del cierre anadieron variables. Marcar cada casilla solo con evidencia (comando, captura, fecha). Detalle ampliado del despliegue: `Paso11_Despliegue_VPS.md`.

---

## Lista de tareas para arrancar el paso a produccion

Resumen ordenado. Cada linea remite a su fase.

| # | Tarea | Donde | Quien | Fase |
| --- | --- | --- | --- | --- |
| 1 | Decidir alcance del primer go-live (recomendado: soft launch, solo invitados, sin Stripe/WA/TG/Calendar) | — | Tu | 0 |
| 2 | Cerrar el codigo del producto minimo (Backlog, filas 5-8: bloques 5-7 y cierre; las filas 0, 1 (P2c 1-3 de la Fase 1.5), 1b (registro de actividad), 2 y 3 (facturas, tickets y reintentos) y 4 (chat) ya estan hechas) y fusionar el PR #1 (`RamaCursor01` → `main`) con CI verde, sin la etiqueta `eval-regression-accepted` | GitHub | Tu | 1 |
| 3 | Cambiar `DATABASE_URL` de Infisical `dev` a `saas_app` y repetir smoke manual (RLS real) | Infisical dev | Tu | 1 |
| 4 | Comprar/elegir **dominio** (p. ej. `app.tudominio.com`) | Registrador DNS | Tu | 2 |
| 5 | Contratar **VPS** (Hetzner u otro UE, 4 vCPU / 8 GB, Ubuntu 24.04) | Proveedor VPS | Tu | 2 |
| 6 | Crear **buckets R2** de prod y de backups + tokens R2 separados | Cloudflare | Tu | 2 |
| 7 | Crear **API keys LLM de prod** (Google, Anthropic, Voyage) con limite de gasto + clave de Google aparte para CI | Consolas proveedores + GitHub | Tu | 2 |
| 8 | **Rotar secretos historicos** expuestos en docs antiguos | Todos los proveedores | Tu | 2 |
| 9 | Endurecer VPS (usuario `deploy`, SSH con clave, ufw) e instalar Docker + Infisical CLI | VPS | Tu | 3 |
| 10 | Crear **Machine Identity** de Infisical para la VPS y su fichero `/etc/iagent/infisical-identity.conf` | Infisical + VPS | Tu | 4 |
| 11 | Rellenar **Infisical `prod`** con secretos nuevos (no copiar `dev`), incluidos SMTP y `EMAIL_SADM` | Infisical | Tu | 5 |
| 12 | Crear **instancia Clerk Production**: DNS, registro restringido, sin creacion de orgs, limite de miembros >= 20, webhook (con `user.deleted`), org SADM | Clerk + DNS | Tu | 6 |
| 13 | Apuntar DNS del dominio a la VPS | Registrador DNS | Tu | 7 |
| 14 | Crear **tag** y ejecutar `deploy.sh` (primer despliegue) | PC + VPS | Tu | 8 |
| 15 | Verificar healthchecks, TLS, login y confirmar `azp` del JWT | Navegador + VPS | Tu | 8 |
| 16 | Programar **backups** (cron) y **probar un restore** fuera de prod | VPS | Tu | 9 |
| 17 | Dar de alta el primer cliente piloto (org Clerk → invitacion → plan en `/sadm/plans` → telefono del admin) | Clerk + SADM | Tu | 10 |
| 18 | **QA manual** en produccion | Navegador | Tu | 11 |
| 19 | Verificacion de **seguridad operativa** | VPS + app | Tu | 12 |
| 20 | **Firma Go/No-Go** y documento de release | `Paso10` | Tu | 13 |
| 21 | Vigilar logs y coste las primeras 48–72 h | VPS + SADM | Tu | 14 |
| 22 | Vigilar la **memoria de Redis** y programar alerta al 80 % (`noeviction`: si se llena, fallan cola, webhooks y cuotas) | VPS | Tu | 14.6 |

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
| Produccion comercial | Todo lo anterior + metodo de cobro de planes decidido e implementado (D016, pendiente) + canales activos con QA real. |

- [ ] Alcance decidido y anotado aqui: ______________________

---

## Fase 1. Preparar el codigo (en tu PC)

### 1.1 Commitear y CI verde

Estado a 2026-09-30: todo commiteado y en `RamaCursor01`; CI y evals verdes en el PR #1. Falta fusionarlo en `main`.

Los tests y los evals usan la BD `saas_test`, nunca `saas` (crearla/migrarla una vez y tras cada migracion nueva):

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

- [ ] `alembic heads` = un unico head (a 2026-10-01: `p81_chat_quota_01`; los bloques 5-7 pueden anadir migraciones: anotar aqui el head final).
- [ ] PR #1 fusionado en `main` con CI verde (quitar antes la etiqueta `eval-regression-accepted`: mientras esta, una bajada real de las evals no falla el job).

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
- [x] Webhooks: limite de body + dedupe anti-replay (Clerk/WA/TG).
- [x] `media_limits` antes de OCR/LLM.
- [x] Sync de memberships Clerk (created/updated/deleted).
- [x] Allowlist `azp`/`aud` obligatoria en staging/production.
- [x] `/docs`, `/redoc`, `/openapi.json` desactivados con `APP_ENV=production`.
- [x] Artefactos de despliegue probados en local (build, stack `production`, migraciones desde cero, backup/restore cifrado).

### 1.5 Seguridad antes de datos de clientes reales (Backlog P2c)

No bloquean el cierre funcional del producto minimo, pero **si la entrada del primer cliente real**: despues los logs y la auditoria ya tendrian datos que no se pueden limpiar facilmente (RGPD).

Codigo de P2c 1-3 hecho el 2026-10-01 (Backlog, fila 1; detalle en `Seguridad_V2.md` §8b). Queda comprobarlo en cada entorno y decidir P2c-7 (datos personales que quedan en la metadata de `audit_log`).

1. **`audit_log` solo insercion (P2c-1).** Migracion `p78_audit_insert_only_01` (`REVOKE UPDATE, DELETE ON audit_log FROM saas_app`). En prod la aplica `deploy.sh` (Fase 8); despues, comprobar con el superusuario. Debe devolver `t | f | f` (puede insertar, no modificar ni borrar):

```powershell
# Dev (contenedor saas-postgres); en la VPS, el mismo SELECT con psql dentro del contenedor postgres.
docker exec -i saas-postgres psql -U saas -d saas -c "SELECT has_table_privilege('saas_app','audit_log','INSERT'), has_table_privilege('saas_app','audit_log','UPDATE'), has_table_privilege('saas_app','audit_log','DELETE');"
```

2. **Datos personales fuera de los logs (P2c-2).** La guardia estatica cubre los `logger.*` de `app/` y los tests de redaccion cubren tracebacks y ARQ:

```powershell
infisical run -- uv run pytest tests/unit/test_logs_no_personal_data.py tests/unit/test_log_redaction.py -q
```

   Tras el primer deploy (Fase 8), subir un documento, provocar un fallo (p. ej. un PDF corrupto) y revisar que los logs de `api` y `worker` no tienen nombres de fichero, importes, emails ni telefonos, y que los tracebacks salen como tipo + `fichero:linea:funcion`, sin mensaje:

```bash
docker compose -f deploy/docker-compose.prod.yml logs --since 15m api worker | grep -iE "filename|@|exception"
```

3. **IP fiable en la auditoria (P2c-3).** Helper unico `app/routes/web/audit_context.py` (`request.client.host`) y `--forwarded-allow-ips=172.30.0.0/24`, la subred fija de la red interna de Compose. Antes del primer deploy, comprobar que esa subred no choca con ninguna red de la VPS (si choca, cambiarla en `networks.default` y en el comando de la API de `deploy/docker-compose.prod.yml`; el test exige que coincidan):

```bash
ip -4 route   # en la VPS: ninguna ruta debe solaparse con 172.30.0.0/24
```

```powershell
infisical run -- uv run pytest tests/unit/test_audit_context.py tests/unit/test_deploy_config.py -q
```

   Tras el deploy, abrir el detalle de un documento y consultar la IP de esa entrada con el superusuario. Debe ser tu IP publica, no `172.30.0.x` (la de Caddy):

```sql
SELECT created_at, action, ip FROM audit_log ORDER BY created_at DESC LIMIT 5;
```

- [x] P2c-1 en codigo; `p78` aplicada en dev y `saas_test` (2026-10-01).
- [ ] P2c-1 en prod: la consulta devuelve `t | f | f`.
- [x] P2c-2 en codigo; guardia y tests verdes (2026-10-01).
- [ ] P2c-2 en prod: logs revisados tras el primer deploy, sin datos personales.
- [x] P2c-3 en codigo; `test_audit_context.py` y `test_deploy_config.py` verdes (2026-10-01).
- [ ] P2c-3 en prod: subred sin conflicto en la VPS e IP real en `audit_log`.
- [ ] P2c-7 decidido (Backlog): metadata de `audit_log` con emails, nombres de fichero y nombres de profesionales y servicios.

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
- [ ] `GOOGLE_API_KEY` **aparte para CI** (Backlog P2b-2), con su propio limite de gasto: en GitHub → repo → Settings → Secrets and variables → Actions, sustituir el secret `GOOGLE_API_KEY` por esta. Nunca usar la de prod en CI.

Motivo: si una clave de dev se filtra no afecta a prod, y el limite del proveedor es un segundo freno de coste ademas de las cuotas de plan. La clave de CI separada evita que una racha de evals consuma la cuota o el presupuesto de Google de los clientes.

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
| `ENCRYPTION_KEY` | Fernet nueva (no la de dev/CI). **Guardar copia en tu gestor de contrasenas**: sin ella no se descifran los tokens de integraciones guardados en BD |

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

**Registro de actividad (D029, implementado 2026-10-01):** en `prod` no hace falta definir nada; los defaults son los buenos:

| Variable | Valor `prod` |
| --- | --- |
| `ACTIVITY_LOG_ENABLED` | sin definir (`true`). Poner `false` solo para apagarlo ante un incidente |
| `ACTIVITY_LOG_RETENTION_DAYS` | sin definir (90) o un valor de 7 o mas. **`0` impide arrancar** |

Con `APP_ENV=production` y `ACTIVITY_LOG_RETENTION_DAYS=0` la app se niega a arrancar con un mensaje que explica el motivo (RGPD: cada fila lleva `user_id`) y como corregirlo. Valores entre 1 y 6 se rechazan en cualquier entorno.

- [x] Decidido que `ACTIVITY_LOG_RETENTION_DAYS=0` se bloquea en produccion (D029, 2026-10-01).
- [x] Implementado y con test (`tests/unit/test_config_activity_log.py`, 2026-10-01).
- [ ] En `prod`, `ACTIVITY_LOG_RETENTION_DAYS` sin definir o >= 7 (revisar en Infisical antes de la Fase 8).
- [ ] Tras el primer deploy (Fase 8), comprobar que se registra actividad y que la purga existe (con el superusuario, en el contenedor `postgres`):

```sql
-- Deben aparecer filas recientes de kind request (api) y job (worker).
SELECT kind, source, count(*), max(occurred_at) FROM activity_log
WHERE occurred_at > now() - interval '1 hour' GROUP BY kind, source;
-- saas_app solo inserta: t | f | f | f
SELECT has_table_privilege('saas_app','activity_log','INSERT'),
       has_table_privilege('saas_app','activity_log','SELECT'),
       has_table_privilege('saas_app','activity_log','UPDATE'),
       has_table_privilege('saas_app','activity_log','DELETE');
```

   Para seguir una peticion concreta (p. ej. la que reporta un usuario con el `X-Request-ID` de la respuesta):

```sql
SELECT occurred_at, kind, source, name, status_code, outcome, location, data
FROM activity_log
WHERE request_id = '<uuid>' OR parent_request_id = '<uuid>'
ORDER BY occurred_at;
```

**Chat (D023, implementado 2026-10-01):** el cupo es `chat_questions_per_month` del plan; no hay variables que definir. El limite de ritmo por usuario usa los defaults (`CHAT_RATE_LIMIT_PER_MINUTE=10`, `CHAT_RATE_LIMIT_PER_HOUR=60`); definirlas solo para cambiarlos.

- [ ] Borradas de Infisical `prod` (y `dev`) `CHAT_DAILY_MESSAGE_LIMIT` y `CHAT_USER_DAILY_MESSAGE_LIMIT`, si existen: ya no se usan (se ignoran, pero confunden).

### 5.8 Email (obligatorio tambien en soft launch)

SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`/`SMTP_SSL`) y `EMAIL_SADM`. Sin SMTP:

- las solicitudes de alta y de baja de miembros desde `/settings/members` fallan (`smtp_not_configured`): la baja es un derecho RGPD del cliente;
- no salen los avisos de presupuesto de IA (80 % al admin, 90 % al SADM, D019), ni el aviso de proveedor sin saldo (D025), ni el de usuarios sin organizacion.

Comprobar tras el deploy: pedir una alta de prueba desde `/settings/members` del tenant piloto y ver que llega a `EMAIL_SADM`.

### 5.9 Opcionales segun alcance (vacias en soft launch)

- WhatsApp: `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`.
- Google Calendar: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` (redirect URI de prod en Google Cloud).
- `METRICS_TOKEN` (Caddy bloquea `/metrics` desde Internet igualmente).

- [ ] Variables 5.1–5.4, 5.6 y 5.8 rellenadas.
- [ ] Ningun valor copiado de `dev`.

---

## Fase 6. Clerk produccion

1. Clerk Dashboard → tu aplicacion → crear instancia **Production**. Dominio: `<dominio>`.
2. Clerk → Domains: crear en tu DNS los **CNAME** que indica (frontend API `clerk.<dominio>`, cuentas, email). Esperar a que Clerk los marque verificados.
3. Configure → Restrictions → **Sign-up mode = Restricted** (solo por invitacion).
4. Configure → Organizations → activar Organizations y **desactivar** que los usuarios creen organizaciones. Limite de miembros por organizacion **>= 20** (maximo de Premium, D022; en dev esta en 5; Backlog P2b-23).
5. Configure → Paths / redirect URLs → `https://app.<dominio>`.
6. Webhooks → Add endpoint `https://app.<dominio>/api/webhooks/clerk`, eventos:
   - `organizationMembership.created`, `organizationMembership.updated`, `organizationMembership.deleted`
   - `user.deleted` (obligatorio: anonimiza el usuario local y desactiva sus membresias, D021)
   - `user.created`, `organization.created` y `organization.deleted` opcionales (crean usuario/tenant antes del primer login; el ultimo solo se registra en el log).
   - Signing secret → `CLERK_WEBHOOK_SECRET` en Infisical `prod`.
7. API Keys → `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY` (de **Production**, `sk_live_`/`pk_live_`). `CLERK_JWKS_URL` = `https://clerk.<dominio>/.well-known/jwks.json` (copiar el que muestra Clerk).
8. `CLERK_JWT_AZP_ALLOWLIST=https://app.<dominio>` (inferido; sin una allowlist la app **no arranca** en production). Se confirma con un JWT real en la Fase 8.
9. Crear la organizacion **SADM**, anadirte como admin → su id (`org_...`) en `ADMIN_CLERK_ORG_ID`; tu user id (`user_...`) en `SUPERADMIN_CLERK_USER_IDS`.

- [ ] Instancia Production con DNS verificado.
- [ ] Registro restringido y creacion de orgs desactivada.
- [ ] Limite de miembros por organizacion >= 20.
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
- [ ] `alembic current` (lo imprime `deploy.sh`) muestra `(head)` y coincide con `alembic heads` del commit desplegado (a 2026-10-01: `p81_chat_quota_01`). Si no pone `(head)`, falta alguna migracion.

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
4. Tu, en `https://app.<dominio>/sadm/plans` → asignar plan (`basic` / `advanced` / `premium`). La primera asignacion se aplica en el acto; los cambios posteriores entran el dia 1 del mes siguiente (D027): elige bien el plan inicial.
5. Comprobar en `/sadm` que aparece la org y su uso.
6. Pedir al admin del tenant que rellene su telefono en `/settings/members` → editar su ficha (Backlog P2b-19, D020). Sin telefono, el mensaje de corte del chat por presupuesto y el aviso al SADM salen solo con su email.

Usuario que entra sin org: ve `/onboarding` y puede avisar al SuperAdmin; no puede crear organizaciones.

- [ ] Primer tenant piloto con plan asignado.
- [ ] Telefono del admin del piloto rellenado.

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
- [ ] Subir un contrato: en la lista se ve la cuota con su periodicidad (p. ej. "95,00 € / mes") y en el detalle firma, cuota, coste anual y total (D024). En el chat, "¿cuanto pago al año en contratos?" responde con el coste anual y "¿que contratos vencen cada mes?" agrupa por vencimiento.
- [ ] En R2 `iagent-prod` los objetos no son publicos (URL directa sin firma → acceso denegado).

### 11.3 Knowledge y chat

- [ ] Subir a knowledge un PDF y una imagen (JPEG/PNG); la indexacion termina en ambos. Confirma que los limites de imagen de la Fase 5.7 son los de produccion (`Paso01` §OCR knowledge).
- [ ] Chat (`/chat`): crear hilo, preguntar por un documento subido, ver las citas; hide thread.
- [ ] Si Langfuse activo: solo metadatos, sin texto de usuario ni de documentos (en soft launch va desactivado; el codigo impide `LANGFUSE_CAPTURE_CONTENT=true` fuera de development).

### 11.4 Planes

Se valida el comportamiento del codigo desplegado (`Planes_Entitlements.md`). Esta fase va despues de los bloques 2-7 del cierre del producto minimo (Backlog fila 9); lo que no se haya implementado de la spec va como gap en la Fase 13 y se tachan aqui sus casillas.

- [ ] Sidebar segun plan; URL directa a feature no incluida → denegada.
- [ ] Cuota bloquea antes de gastar LLM.
- [ ] Primera asignacion de plan en `/sadm/plans` inmediata; un segundo cambio queda programado para el dia 1 del mes siguiente (D027).
- [ ] Ampliacion de un cupo del mes desde `/sadm/plans/tenants/{id}` se refleja en el cupo y queda en `audit_log` (`sadm.quota_extra_added`).
- [ ] Facturas + tickets (bloque 2), con un tenant de prueba y un override bajo en `/sadm/plans` (p. ej. `invoices_per_month` = 2 y `tickets_per_month` = 0):
  - Subir el mismo fichero dos veces → la segunda vez sale "ya está subido como factura... No se ha vuelto a procesar ni consume cupo" y no aparece una fila nueva.
  - Subir 3 facturas distintas → las 2 primeras se procesan; la tercera sale como "Pendiente de cupo" con aviso al subir, y el admin recibe el email del 80 % y el de cupo agotado (uno de cada al mes).
  - Ampliar el cupo de facturas en 1 desde `/sadm/plans/tenants/{id}` → en unos segundos la pendiente pasa a procesarse (recargar el panel: la fila pendiente no hace polling).
  - Borrar una factura procesada este mes → el consumo del mes baja en 1.
  - Comprobar que el consumo cuadra con los documentos reservados (debe salir la misma cifra en las dos columnas):

```sql
SELECT
  (SELECT COALESCE(SUM(used), 0) FROM quota_usage
    WHERE tenant_id = '<tenant>' AND period = date_trunc('month', now() AT TIME ZONE 'Europe/Madrid')::date
      AND code IN ('invoices_per_month', 'tickets_per_month')) AS consumo,
  (SELECT count(*) FROM invoices WHERE tenant_id = '<tenant>'
      AND quota_period = date_trunc('month', now() AT TIME ZONE 'Europe/Madrid')::date)
  + (SELECT count(*) FROM tickets WHERE tenant_id = '<tenant>'
      AND quota_period = date_trunc('month', now() AT TIME ZONE 'Europe/Madrid')::date) AS reservados;
```

- [ ] Reintentos (bloque 3): con un documento que falla siempre (p. ej. una imagen sin texto subida como factura), "Reintentar" 3 veces → la fila muestra "Revision manual" y ya no ofrece "Reintentar". Con `document_retries_per_month` = 0 por override, "Reintentar" responde "Has agotado los reintentos de procesado de este mes".
- [ ] Chat (bloque 4), con un tenant de prueba y un override bajo de `chat_questions_per_month` en `/sadm/plans` (p. ej. 2):
  - Hacer 2 preguntas → se responden. La tercera recibe "Has alcanzado las preguntas de este mes. Se renuevan el 1 de <mes>..." con el telefono y el email del admin, sin coste nuevo en `llm_calls`.
  - Ampliar el cupo del chat en 1 desde `/sadm/plans/tenants/{id}` → la siguiente pregunta se responde.
  - Enviar mas de 10 preguntas en un minuto (p. ej. pulsando Enviar seguido) → "Estás enviando preguntas muy seguidas..." y el mensaje no se guarda.
  - Comprobar el consumo del mes (debe coincidir con las preguntas respondidas):

```sql
SELECT used, extra FROM quota_usage
WHERE tenant_id = '<tenant>' AND code = 'chat_questions_per_month'
  AND period = date_trunc('month', now() AT TIME ZONE 'Europe/Madrid')::date;
```
- [ ] Contratos (bloque 5): "Marcar como sustituido" libera el hueco de activo; un contrato de mas de 100 paginas se rechaza.
- [ ] Historico (bloque 6): en Basico no se ven facturas ni tickets de hace mas de 12 meses.
- [ ] "Mi cuenta" (bloque 7) muestra "X de Y" de cada cupo.
- [ ] `/settings/members` muestra "Miembros: X de Y"; en Basico (3) se rechaza el alta del 4.º miembro y aparece el aviso de maximo alcanzado (D022). Recordar el limite de Clerk >= 20 (Fase 6).

### 11.5 Canales, calendario y billing (solo si entran en el alcance)

- [ ] Google OAuth con callback de prod; voz → evento.
- [ ] WhatsApp / Telegram con firma real y replay = no-op (`Paso01` §4).
- [ ] Citas por WhatsApp / Telegram solo con `Paso12` cerrado (D030): validacion manual de su §9 (reserva, aislamiento entre clientes, antelacion minima de 24 h, cita del centro vista por WhatsApp, cache sin datos de citas).
- [ ] Cobro de planes: pendiente de decidir metodo (D016, Backlog P3-2). Stripe retirado; no configurar `STRIPE_*`.

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
- [ ] Riesgos aceptados por escrito. Candidatos a 2026-09-30 (quitar los que ya esten resueltos al firmar):
  - Langfuse prod aplazado (sin tracing; el coste sigue en `llm_calls`).
  - CSP con `unsafe-inline`/`unsafe-eval` por Alpine.
  - Sin staging: el primer despliegue va directo a prod (mitigacion: backup de `deploy.sh` + restore probado en Fase 9).
  - Solo si algun bloque 2-7 del cierre no esta hecho al firmar: parte de la spec de planes sin implementar (p. ej. contratos, `history_months` o el consumo en "Mi cuenta"). El presupuesto de IA 6/15/30 € ya esta aplicado (D026).
  - Presupuesto de IA sin ampliacion mensual: solo override permanente del SADM, a retirar a mano (Backlog P2b-27).
  - Documentos pendientes de cupo sin tope por tenant: el unico freno es el tope diario alto de subidas (Backlog P2b-28).
  - Cobro de planes fuera de la app (D016): factura manual, plan asignado por SADM.
  - WhatsApp/Telegram, Google Calendar y voz sin QA real (fuera de alcance).
  - Knowledge sin limites mensuales: mantiene `knowledge_docs_max` y `knowledge_uploads_per_day` actuales porque la spec de planes aun no los define (§11).
  - Renovacion enlazada automatica de contratos y purga a los 30 dias (D027, Backlog P3-8): solo boton manual "Marcar como sustituido" y borrado inmediato.
- [ ] Firma en `Paso10_QA_Release_Produccion.md` (seccion "Registro de releases", con su plantilla).

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

### 14.6 Memoria de Redis llena (riesgo detectado 2026-09-28)

Redis de prod arranca con `--maxmemory 512mb --maxmemory-policy noeviction` (`deploy/docker-compose.prod.yml`). Es a proposito: **no** expulsa claves, porque guarda la cola ARQ, el anti-replay de webhooks y los contadores de cuotas. La contrapartida: si llega a 512 MB, **toda escritura falla** (`OOM command not allowed`) y la app empieza a dar errores:

- no se encolan jobs (documentos y conocimiento se quedan sin procesar);
- los webhooks (Clerk, WhatsApp, Telegram) fallan al registrar el anti-replay;
- los limites diarios y de ritmo que siguen en Redis no pueden contar (subidas y chat devuelven error). Los cupos mensuales estan en PostgreSQL (`quota_usage`).

Con el trafico previsto es improbable, pero no hay aviso previo: hay que vigilarlo.

- [ ] Comprobar el uso de memoria tras el primer despliegue y en la revision semanal. `used_memory_human` debe quedar muy por debajo de `maxmemory_human`:

```bash
docker compose -f /opt/iagent/deploy/docker-compose.prod.yml exec redis \
  redis-cli INFO memory | grep -E 'used_memory_human|maxmemory_human|maxmemory_policy'
```

- [ ] Programar una alerta por encima del 80 % (cron cada 15 min en la VPS, aviso a `EMAIL_SADM` o al canal de incidencias). Comando base para el script de alerta (imprime el % usado):

```bash
docker compose -f /opt/iagent/deploy/docker-compose.prod.yml exec -T redis \
  sh -c 'redis-cli INFO memory | awk -F: "/^used_memory:/{u=\$2} /^maxmemory:/{m=\$2} END{printf \"%d\n\", u*100/m}"'
```

- [ ] Si se acerca al limite, **antes** de subir `--maxmemory`, ver que ocupa (`redis-cli --bigkeys`, `redis-cli INFO keyspace`): una cola ARQ atascada (worker parado) o claves sin TTL crecen sin fin. Subir `maxmemory` en el compose solo si el crecimiento es trafico real, y comprobar que la VPS tiene RAM libre.
- [ ] **No** cambiar la politica a `allkeys-lru` (la de dev): expulsaria jobs de la cola y claves anti-replay sin avisar.

---

## Aplazado (fuera del soft launch)

| Tema | Que hace falta |
| --- | --- |
| Langfuse prod | Instancia self-hosted (web, worker, ClickHouse, Redis, S3) o decision alternativa en `Decision_Log`; claves en Infisical |
| Cobro de planes | Decidir metodo de cobro (D016, Backlog P3-2). Stripe retirado del codigo; el plan lo asigna el SADM en `/sadm/plans` |
| WhatsApp / Telegram | Credenciales, webhook a URL prod, QA real + replay; citas por canal con `Paso12` cerrado (D030) |
| Google Calendar / voz | OAuth client de prod con redirect URI; no se publicita (D012) |
| CSP estricta | Migrar a `@alpinejs/csp` y quitar `unsafe-inline`/`unsafe-eval` (Paso01 §6) |
| Staging | Mismo compose en otra VPS con Infisical `staging` (secretos propios, `APP_ENV=staging`) para ensayar releases. Hasta entonces el entorno `staging` de Infisical queda vacio |

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
