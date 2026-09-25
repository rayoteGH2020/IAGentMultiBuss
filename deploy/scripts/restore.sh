#!/usr/bin/env bash
# Restaura un backup cifrado sobre la BD de producción. DESTRUCTIVO.
#
# Uso:
#   sudo bash deploy/scripts/restore.sh /var/backups/iagent/saas-XXXX.dump.gpg --yes-destroy-data
#
# Para API y worker, restaura (pg_restore --clean) y los vuelve a arrancar.
# Para probar un restore sin tocar prod, hacerlo en otra VPS/instancia vacía.

# shellcheck source=deploy/scripts/_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

DUMP="${1:-}"
[[ -f "$DUMP" ]] || die "uso: restore.sh <fichero.dump.gpg> --yes-destroy-data"
[[ "${2:-}" == "--yes-destroy-data" ]] \
    || die "falta --yes-destroy-data: esto sobrescribe la BD de producción"

require_cmd docker infisical gpg
load_identity
cd "$REPO_DIR"
export RESTORE_FILE="$DUMP"

log "parando API y worker"
with_secrets docker compose stop api worker caddy

log "restaurando $DUMP"
with_secrets bash -c '
    set -euo pipefail
    : "${BACKUP_ENCRYPTION_PASSPHRASE:?}"
    gpg --batch --pinentry-mode loopback --passphrase-fd 3 --decrypt "$RESTORE_FILE" \
        3<<<"$BACKUP_ENCRYPTION_PASSPHRASE" \
        | docker compose exec -T postgres \
            sh -c "pg_restore -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" --clean --if-exists --single-transaction"
'

log "arrancando de nuevo"
with_secrets docker compose up -d --wait
log "restore OK. Comprobar login y alembic current."
