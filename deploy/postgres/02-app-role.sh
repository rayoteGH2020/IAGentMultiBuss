#!/usr/bin/env bash
# Crea el rol de aplicación saas_app con contraseña de Infisical ANTES de las
# migraciones. p06_saas_app_03 hace CREATE ROLE ... IF NOT EXISTS con la
# contraseña de desarrollo 'saas'; al existir ya, no la sobrescribe.
#
# Lo ejecuta el entrypoint de Postgres solo con el volumen vacío. El entrypoint
# puede hacer `source` de este fichero (si no es ejecutable): no usar `set`
# ni `exit` aquí.
: "${SAAS_APP_DB_PASSWORD:?SAAS_APP_DB_PASSWORD missing}"

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=app_password="$SAAS_APP_DB_PASSWORD" <<'SQL'
CREATE ROLE saas_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOBYPASSRLS INHERIT;
SQL
