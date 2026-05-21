"""identity tables

Revision ID: p06_identity_01
Revises:
Create Date: 2026-05-13

Primera migración de la cadena (down_revision=None): crea las tres tablas
de identidad y multi-tenancy sobre las que se apoyan todos los módulos.
El orden de creación importa por las FK: tenants → users → memberships.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p06_identity_01"
# down_revision=None: raíz de la cadena de migraciones; no hay migración previa.
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # clerk_org_id nullable: permite crear tenants en BD antes de que
        # el webhook de Clerk haya sincronizado el org_id, o para tenants
        # de test que no tienen organización Clerk.
        sa.Column("clerk_org_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        # plan: nivel de suscripción. server_default="free" asegura que los
        # tenants nuevos empiecen con el plan gratuito sin intervención manual.
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="free"),
        # settings JSONB con default '{}': almacena configuración flexible del
        # tenant (p. ej. idioma, módulos activos, límites custom). El default
        # es un objeto vacío, no NULL, para que el código pueda hacer
        # settings.get("key") sin comprobar si settings es None.
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Único: cada organización Clerk mapea exactamente a un tenant interno.
    # El índice también acelera la resolución de tenant en cada request
    # (AuthMiddleware busca por clerk_org_id tras validar el JWT).
    op.create_index(op.f("ix_tenants_clerk_org_id"), "tenants", ["clerk_org_id"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # clerk_user_id nullable por la misma razón que clerk_org_id en tenants.
        sa.Column("clerk_user_id", sa.String(length=64), nullable=True),
        # email único: identificador de fallback cuando clerk_user_id no está
        # disponible y para mostrar al usuario en la UI.
        sa.Column("email", sa.String(length=255), nullable=False),
        # name nullable: Clerk no obliga a los usuarios a tener nombre completo.
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # clerk_user_id único: un usuario Clerk no puede tener dos registros locales.
    op.create_index(op.f("ix_users_clerk_user_id"), "users", ["clerk_user_id"], unique=True)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # role: "admin" | "member" | "viewer". server_default="member" para que
        # las invitaciones básicas no necesiten especificar el rol explícitamente.
        sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # CASCADE en ambas FK: si se elimina un tenant o un usuario, sus
        # memberships se borran automáticamente, sin dejar registros huérfanos.
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Restricción única compuesta: un usuario solo puede tener una membership
        # por tenant. Impide duplicados si el webhook de Clerk dispara dos veces.
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
    )
    # Dos índices en memberships porque las queries van en ambas direcciones:
    # "todos los tenants de un usuario" (login) y "todos los usuarios de un tenant"
    # (panel de administración de miembros).
    op.create_index(op.f("ix_memberships_tenant_id"), "memberships", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)


def downgrade() -> None:
    # Orden inverso al upgrade y respetando dependencias FK:
    # memberships depende de users y tenants → se borra primero.
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_tenant_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_clerk_user_id"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_tenants_clerk_org_id"), table_name="tenants")
    op.drop_table("tenants")
