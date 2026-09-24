#!/usr/bin/env bash
# Crea (si no existe) la BD de tests `saas_test` en el Postgres de desarrollo y
# la migra a head. La app sigue usando `saas`; los tests usan siempre
# `saas_test` (tests/db_target.py + tests/conftest.py).
#
# Uso (repetible; también tras añadir migraciones nuevas):
#   infisical run -- bash scripts/test_db_setup.sh
#
# Variables opcionales: PG_CONTAINER (default saas-postgres), PG_SUPERUSER (default saas).
set -euo pipefail
cd "$(dirname "$0")/.."

PG_CONTAINER="${PG_CONTAINER:-saas-postgres}"
PG_SUPERUSER="${PG_SUPERUSER:-saas}"
TEST_DB="saas_test"

psql_admin() {
    docker exec -i "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$PG_SUPERUSER" "$@"
}

if [[ "$(psql_admin -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$TEST_DB'")" != "1" ]]; then
    echo "Creando BD $TEST_DB"
    psql_admin -d postgres -c "CREATE DATABASE $TEST_DB OWNER $PG_SUPERUSER"
fi

# Mismas extensiones que docker/postgres/init.sql (solo corre en la BD inicial).
psql_admin -d "$TEST_DB" -q <<'SQL'
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";
SQL

: "${DATABASE_URL:?DATABASE_URL missing: ejecutar con infisical run}"
TEST_URL="$(uv run python -m tests.db_target)"
echo "Migrando $TEST_DB a head"
DATABASE_URL="$TEST_URL" uv run alembic upgrade head
DATABASE_URL="$TEST_URL" uv run alembic current
