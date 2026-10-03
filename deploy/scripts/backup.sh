#!/usr/bin/env bash
# Backup cifrado de Postgres: pg_dump -Fc → gpg (AES256) → disco local + R2.
#
# Uso: sudo bash deploy/scripts/backup.sh
# Cron diario (ver Paso11 §7). Variables en Infisical prod:
#   BACKUP_ENCRYPTION_PASSPHRASE   guardarla también fuera de la VPS (gestor de
#                                  contraseñas): sin ella el backup no sirve.
#   BACKUP_R2_BUCKET, BACKUP_R2_ACCESS_KEY_ID, BACKUP_R2_SECRET_ACCESS_KEY
#                                  bucket y token R2 distintos de los de la app.
#   R2_ACCOUNT_ID                  el mismo de la app.

# shellcheck source=deploy/scripts/_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

LOCAL_RETENTION_DAYS="${IAGENT_BACKUP_RETENTION_DAYS:-7}"
AWS_CLI_IMAGE="amazon/aws-cli:2.27.0"

require_cmd docker infisical gpg
load_identity
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="saas-$STAMP.dump.gpg"
export BACKUP_FILE="$BACKUP_DIR/$FILE" BACKUP_NAME="$FILE" BACKUP_DIR AWS_CLI_IMAGE

log "pg_dump → $BACKUP_FILE"
with_secrets bash -c '
    set -euo pipefail
    : "${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE missing in Infisical}"
    umask 077
    docker compose exec -T postgres \
        sh -c "pg_dump -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Fc" \
        | gpg --batch --yes --pinentry-mode loopback --passphrase-fd 3 \
            --symmetric --cipher-algo AES256 -o "$BACKUP_FILE" \
            3<<<"$BACKUP_ENCRYPTION_PASSPHRASE"
    [[ -s "$BACKUP_FILE" ]] || { echo "backup vacío" >&2; exit 1; }
'

log "subiendo a R2"
with_secrets bash -c '
    set -euo pipefail
    : "${BACKUP_R2_BUCKET:?}" "${BACKUP_R2_ACCESS_KEY_ID:?}" "${BACKUP_R2_SECRET_ACCESS_KEY:?}"
    : "${R2_ACCOUNT_ID:?}"
    AWS_ACCESS_KEY_ID="$BACKUP_R2_ACCESS_KEY_ID" \
    AWS_SECRET_ACCESS_KEY="$BACKUP_R2_SECRET_ACCESS_KEY" \
    docker run --rm \
        -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
        -e AWS_DEFAULT_REGION=auto \
        -e AWS_REQUEST_CHECKSUM_CALCULATION=when_required \
        -e AWS_RESPONSE_CHECKSUM_VALIDATION=when_required \
        -v "$BACKUP_DIR:/backups:ro" \
        "$AWS_CLI_IMAGE" s3 cp "/backups/$BACKUP_NAME" "s3://$BACKUP_R2_BUCKET/postgres/$BACKUP_NAME" \
        --endpoint-url "https://$R2_ACCOUNT_ID.r2.cloudflarestorage.com" --only-show-errors
'

find "$BACKUP_DIR" -maxdepth 1 -name 'saas-*.dump.gpg' -mtime "+$LOCAL_RETENTION_DAYS" -delete
log "backup OK: $FILE (local $LOCAL_RETENTION_DAYS días; retención R2 por lifecycle rule)"
