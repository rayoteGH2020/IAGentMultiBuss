#!/usr/bin/env bash
# Utilidades comunes de deploy/backup/restore. Se carga con `source`.
#
# Único secreto fuera de Infisical: la Machine Identity (Universal Auth) de la
# VPS, en IDENTITY_FILE (root:root, 0600). Solo lectura sobre el entorno prod.
# Formato (sin comillas, una por línea):
#   INFISICAL_UNIVERSAL_AUTH_CLIENT_ID=...
#   INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET=...
#   INFISICAL_PROJECT_ID=...
#   INFISICAL_ENV=prod
#   INFISICAL_API_URL=https://eu.infisical.com/api   (opcional, según región)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IDENTITY_FILE="${IAGENT_IDENTITY_FILE:-/etc/iagent/infisical-identity.conf}"
BACKUP_DIR="${IAGENT_BACKUP_DIR:-/var/backups/iagent}"
# docker compose lee COMPOSE_FILE: todos los `docker compose` usan el de prod.
export COMPOSE_FILE="$REPO_DIR/deploy/docker-compose.prod.yml"

log() {
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

die() {
    log "ERROR: $*"
    exit 1
}

require_cmd() {
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || die "falta el comando '$cmd'"
    done
}

load_identity() {
    [[ -f "$IDENTITY_FILE" ]] || die "no existe $IDENTITY_FILE (ver Paso11 §3)"
    local perms
    perms="$(stat -c '%a' "$IDENTITY_FILE")"
    [[ "$perms" == "600" || "$perms" == "400" ]] \
        || die "$IDENTITY_FILE debe tener permisos 600 (tiene $perms)"
    local key value
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" == \#* ]] && continue
        case "$key" in
            INFISICAL_UNIVERSAL_AUTH_CLIENT_ID | INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET | \
                INFISICAL_PROJECT_ID | INFISICAL_ENV | INFISICAL_API_URL)
                export "$key=$value"
                ;;
            *) die "clave no permitida en $IDENTITY_FILE: $key" ;;
        esac
    done <"$IDENTITY_FILE"
    : "${INFISICAL_UNIVERSAL_AUTH_CLIENT_ID:?}" "${INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET:?}"
    : "${INFISICAL_PROJECT_ID:?}"
    INFISICAL_ENV="${INFISICAL_ENV:-prod}"
}

# Ejecuta un comando con los secretos de Infisical prod en el entorno.
# El client secret viaja por variable de entorno, no por argv (no sale en `ps`).
with_secrets() {
    if [[ -z "${INFISICAL_TOKEN:-}" ]]; then
        INFISICAL_TOKEN="$(infisical login --method=universal-auth --silent --plain)"
        export INFISICAL_TOKEN
    fi
    infisical run --silent --env="$INFISICAL_ENV" --projectId="$INFISICAL_PROJECT_ID" -- "$@"
}
