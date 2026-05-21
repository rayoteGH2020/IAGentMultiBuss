"""create saas_app role for RLS tests and app connections

Revision ID: p06_saas_app_03
Revises: p06_rls_02
Create Date: 2026-05-13

La app y los workers se conectan a Postgres con el rol `saas_app`, nunca como
superusuario. Esto es lo que hace que RLS sea efectivo: un superusuario ignora
las políticas RLS; saas_app (NOBYPASSRLS) las respeta.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p06_saas_app_03"
down_revision: str | None = "p06_rls_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text("""
        DO $$
        BEGIN
            -- IF NOT EXISTS: hace la migración idempotente. Si el rol ya existe
            -- (p. ej. en una BD de dev recreada parcialmente), no falla.
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'saas_app') THEN
                CREATE ROLE saas_app
                    LOGIN                -- puede abrir conexiones (no es solo un grupo)
                    PASSWORD 'saas'      -- contraseña de desarrollo; en producción
                                         -- Infisical inyecta DATABASE_URL con la real
                    NOSUPERUSER          -- principio de mínimo privilegio
                    NOBYPASSRLS          -- CRÍTICO: RLS se aplica a este rol.
                                         -- Sin NOBYPASSRLS el rol ignoraría las políticas
                                         -- de tenant_isolation y vería datos de todos
                    INHERIT;             -- hereda privilegios de los grupos a los que pertenezca
            END IF;
        END
        $$;
        """)
    )
    # USAGE en schema: necesario para que saas_app pueda "ver" los objetos del
    # schema public. Sin esto, ni siquiera puede listar las tablas.
    op.execute(text("GRANT USAGE ON SCHEMA public TO saas_app"))
    # DML completo sobre todas las tablas existentes en el momento de la migración.
    # TRUNCATE se incluye aquí para que los tests de integración puedan limpiar
    # la BD entre ejecuciones (TRUNCATE es más rápido que DELETE en tablas grandes).
    op.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public TO saas_app"))
    # USAGE + SELECT en sequences: necesario para columnas con nextval() aunque
    # las PKs sean UUID; algunas tablas auxiliares o de Langfuse pueden usar SERIAL.
    op.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO saas_app"))
    # ALTER DEFAULT PRIVILEGES: garantiza que las tablas y sequences creadas por
    # migraciones FUTURAS también tengan los permisos correctos automáticamente,
    # sin necesidad de añadir un GRANT explícito en cada nueva migración.
    op.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO saas_app"
        )
    )
    op.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO saas_app"
        )
    )


def downgrade() -> None:
    # Orden: primero revocar privilegios, luego eliminar el rol.
    # Postgres no permite DROP ROLE si el rol tiene privilegios sobre objetos.
    op.execute(text("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM saas_app"))
    op.execute(text("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM saas_app"))
    op.execute(text("REVOKE USAGE ON SCHEMA public FROM saas_app"))
    op.execute(text("DROP ROLE IF EXISTS saas_app"))
