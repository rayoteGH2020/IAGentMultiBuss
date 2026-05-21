"""enable RLS on tenant tables

Revision ID: p06_rls_02
Revises: p06_identity_01
Create Date: 2026-05-13

Separar la creación de tablas (p06_identity_01) de la activación de RLS permite
revertir cada paso de forma independiente y deja clara la intención en el historial
de migraciones.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "p06_rls_02"
down_revision: str | None = "p06_identity_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Solo memberships: tenants y users NO tienen tenant_id propio (son entidades
# raíz, no datos de cliente) y por tanto no necesitan RLS de aislamiento por
# tenant. Las tablas de módulos (invoices, llm_calls) activan RLS en sus
# propias migraciones para mantener la cohesión.
TENANT_TABLES = ("memberships",)


def upgrade() -> None:
    for tbl in TENANT_TABLES:
        # ENABLE ROW LEVEL SECURITY: activa RLS para conexiones que no son el
        # propietario de la tabla (el usuario de la app, saas_app).
        op.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;"))
        # FORCE ROW LEVEL SECURITY: extiende RLS también al propietario de la
        # tabla. Sin esto, el rol que creó la tabla (normalmente el superusuario
        # o el rol de migraciones) ignoraría las políticas, abriendo una brecha.
        op.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;"))
        op.execute(
            text(
                f"""
            CREATE POLICY tenant_isolation ON {tbl}
            -- USING controla qué filas son VISIBLES en SELECT y eliminables en
            -- DELETE. Si la condición es false, la fila simplemente no existe
            -- para ese contexto de sesión (no devuelve error, devuelve vacío).
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            -- WITH CHECK controla qué filas pueden ESCRIBIRSE (INSERT/UPDATE).
            -- Si la condición es false, la operación falla con permiso denegado.
            -- Tener ambas cláusulas garantiza aislamiento completo de lectura Y escritura.
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """
            )
            # Notas sobre la condición:
            # - tenant_id::text: current_setting siempre devuelve text; el cast
            #   evita error de tipos al comparar UUID con text.
            # - current_setting('app.current_tenant', true): el segundo argumento
            #   `true` hace que devuelva NULL en lugar de lanzar error si la
            #   variable de sesión no está seteada (p. ej. en la propia migración
            #   o en queries de administración). NULL != text → fila no visible,
            #   comportamiento seguro por defecto (fail closed).
        )


def downgrade() -> None:
    for tbl in TENANT_TABLES:
        # IF EXISTS evita error si la política ya fue eliminada manualmente.
        op.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl};"))
        op.execute(text(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;"))
