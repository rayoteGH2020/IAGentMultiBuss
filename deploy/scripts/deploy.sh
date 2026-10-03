#!/usr/bin/env bash
# Despliega un tag/commit en la VPS.
#
# Uso:
#   sudo bash deploy/scripts/deploy.sh <tag|sha>                 # deploy normal
#   sudo bash deploy/scripts/deploy.sh <tag|sha> --no-migrate    # rollback de código
#
# Orden: checkout → build → Postgres/Redis → backup → alembic → API + worker.
# --no-migrate solo es seguro si el esquema actual es compatible con ese código;
# si no, restaurar backup (restore.sh). No usar `alembic downgrade` (p64 hace
# DROP TABLE plans).

# shellcheck source=deploy/scripts/_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

REF="${1:-}"
MIGRATE=1
[[ -n "$REF" ]] || die "uso: deploy.sh <tag|sha> [--no-migrate]"
if [[ "${2:-}" == "--no-migrate" ]]; then
    MIGRATE=0
elif [[ -n "${2:-}" ]]; then
    die "argumento desconocido: $2"
fi

require_cmd git docker infisical
load_identity
cd "$REPO_DIR"

[[ -z "$(git status --porcelain --untracked-files=no)" ]] \
    || die "el checkout de la VPS tiene cambios locales; no se despliega"

log "fetch y checkout de $REF"
git fetch --tags --prune origin
git rev-parse --verify --quiet "$REF^{commit}" >/dev/null || die "ref desconocida: $REF"
git checkout --quiet --detach "$REF"
APP_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
export APP_IMAGE_TAG

log "build de la imagen iagent-app:$APP_IMAGE_TAG"
with_secrets docker compose build api

log "arrancando Postgres y Redis"
with_secrets docker compose up -d --wait postgres redis

log "backup previo"
bash "$REPO_DIR/deploy/scripts/backup.sh"

if [[ "$MIGRATE" == "1" ]]; then
    log "alembic upgrade head (rol propietario MIGRATIONS_DATABASE_URL)"
    # La app usa DATABASE_URL = saas_app (NOBYPASSRLS); las migraciones, el
    # propietario. Se sobrescribe DATABASE_URL solo para este contenedor.
    with_secrets bash -c '
        : "${MIGRATIONS_DATABASE_URL:?MIGRATIONS_DATABASE_URL missing in Infisical}"
        DATABASE_URL="$MIGRATIONS_DATABASE_URL" \
            docker compose run --rm --no-deps api alembic upgrade head
        DATABASE_URL="$MIGRATIONS_DATABASE_URL" \
            docker compose run --rm --no-deps api alembic current
    '
else
    log "migraciones omitidas (--no-migrate)"
fi

log "arrancando API, worker y Caddy"
with_secrets docker compose up -d --wait --remove-orphans

mkdir -p "$BACKUP_DIR"
printf '%s\t%s\t%s\tmigrate=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$REF" "$APP_IMAGE_TAG" "$MIGRATE" \
    >>"$BACKUP_DIR/releases.log"
log "deploy OK: $REF ($APP_IMAGE_TAG). Historial: $BACKUP_DIR/releases.log"
