# Paso 06 — Modelos de identidad, Alembic y RLS

## Objetivo

Crear los modelos de identidad (`Tenant`, `User`, `Membership`), inicializar Alembic, generar la primera migración y activar **Row-Level Security** en Postgres con un test que verifica el aislamiento entre tenants.

Al final del paso, hay tres tenants en BD, sus datos están aislados por RLS, y un test demuestra que un tenant no puede leer datos del otro aunque la query no incluya `WHERE tenant_id`.

## Pre-requisitos

- Pasos 01-05 completados.
- Postgres corriendo con `pgvector` y `pgcrypto`.

## Contexto relevante

- `arquitectura.md` sección 5 (Modelo de datos) y sección 9 (Seguridad y multi-tenancy).
- `Agents.md` sección 7 (Multi-tenancy y seguridad).

## Tareas

- [x] Crear `app/models/base.py` con `Base` y `TimestampMixin`.
- [x] Crear `app/models/tenant.py` con modelo `Tenant`.
- [x] Crear `app/models/user.py` con modelo `User`.
- [x] Crear `app/models/membership.py` con modelo `Membership`.
- [x] Re-exportar todos en `app/models/__init__.py`.
- [x] Inicializar Alembic: `uv run alembic init -t async migrations`.
- [x] Configurar `alembic.ini` y `migrations/env.py` para usar `Settings` y `Base`.
- [x] Generar primera migración con autogenerate.
- [x] Revisar la migración a mano.
- [x] Crear segunda migración que activa RLS en las tablas con `tenant_id`.
- [x] Aplicar migraciones con `alembic upgrade head`.
- [x] Implementar `app/core/db.py::set_tenant_context()` para `SET LOCAL app.current_tenant`.
- [x] Crear test de integración que verifica aislamiento RLS.
- [x] Crear `scripts/seed_dev.py` para datos de prueba en dev.
- [x] Commit: `feat: identity models with RLS isolation`.

## Detalles técnicos

### `app/models/base.py`

```python
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class IdMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


__all__ = ["Base", "IdMixin", "TimestampMixin"]
```

### `app/models/tenant.py`

```python
from typing import TYPE_CHECKING

from sqlalchemy import JSON, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.membership import Membership


class Tenant(Base, IdMixin, TimestampMixin):
    __tablename__ = "tenants"

    clerk_org_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="tenant")
```

### `app/models/user.py`

```python
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.membership import Membership


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    clerk_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")
```

### `app/models/membership.py`

```python
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.tenant import Tenant
from app.models.user import User


class Membership(Base, IdMixin, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member", nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
```

### `app/models/__init__.py`

```python
from app.models.base import Base
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User

__all__ = ["Base", "Membership", "Tenant", "User"]
```

### Inicializar Alembic

```bash
uv run alembic init -t async migrations
```

### `alembic.ini`

Editar solo:
```ini
sqlalchemy.url =
# Lo dejamos vacío, lo seteamos en env.py desde Settings
```

### `migrations/env.py`

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings
from app.models import Base
# IMPORTANTE: importar todos los modelos para que autogenerate los detecte
from app.models import membership, tenant, user  # noqa: F401

config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### Generar primera migración

```bash
uv run alembic revision --autogenerate -m "identity tables"
```

Revisar `migrations/versions/<hash>_identity_tables.py`. Debe crear `tenants`, `users`, `memberships`. Si Alembic intenta crear extensiones, las dejamos (ya están del init.sql pero `CREATE EXTENSION IF NOT EXISTS` no rompe).

### Generar segunda migración para RLS (manual)

```bash
uv run alembic revision -m "enable RLS on tenant tables"
```

Editar `migrations/versions/<hash>_enable_rls_on_tenant_tables.py`:

```python
"""enable RLS on tenant tables

Revision ID: ...
Revises: <id de identity tables>
Create Date: ...
"""
from alembic import op


revision = "..."
down_revision = "..."  # <- el id de identity_tables
branch_labels = None
depends_on = None


# Tablas que tendrán tenant_id (incluyendo las futuras)
TENANT_TABLES = ["memberships"]  # añadiremos invoices, etc. en pasos posteriores


def upgrade() -> None:
    for tbl in TENANT_TABLES:
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {tbl}
            USING (tenant_id::text = current_setting('app.current_tenant', true))
            WITH CHECK (tenant_id::text = current_setting('app.current_tenant', true));
        """)


def downgrade() -> None:
    for tbl in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl};")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;")
```

> **Nota**: `tenants` y `users` NO tienen `tenant_id`, son tablas globales (un usuario puede pertenecer a varios tenants). El aislamiento se aplica a partir de `memberships` y todas las tablas de dominio.

### Aplicar migraciones

```bash
uv run alembic upgrade head
```

Verificar en `psql`:

```sql
\d tenants
\d users
\d memberships

SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'memberships';
SELECT * FROM pg_policies WHERE tablename = 'memberships';
```

### Helper para setear contexto RLS

Añadir a `app/core/db.py`:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    """Configura el contexto RLS para la sesión actual."""
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )


async def clear_tenant_context(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.current_tenant', '', true)"))
```

### Test de aislamiento

`tests/integration/test_rls_isolation.py`:

```python
import pytest
from sqlalchemy import select

from app.core.db import session_scope, set_tenant_context
from app.models import Membership, Tenant, User

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_membership_rls_isolation():
    """Verifica que dos tenants no se ven mutuamente."""
    # Setup: dos tenants con un user y una membership cada uno
    async with session_scope() as s:
        t1 = Tenant(name="Tenant 1")
        t2 = Tenant(name="Tenant 2")
        u1 = User(email="u1@test.com", name="U1")
        u2 = User(email="u2@test.com", name="U2")
        s.add_all([t1, t2, u1, u2])
        await s.flush()

        m1 = Membership(user_id=u1.id, tenant_id=t1.id, role="admin")
        m2 = Membership(user_id=u2.id, tenant_id=t2.id, role="admin")
        s.add_all([m1, m2])
        await s.flush()
        t1_id, t2_id = t1.id, t2.id

    # Test: con contexto = t1, solo veo membership de t1
    async with session_scope() as s:
        await set_tenant_context(s, str(t1_id))
        result = await s.execute(select(Membership))
        memberships = result.scalars().all()
        assert len(memberships) == 1
        assert memberships[0].tenant_id == t1_id

    # Test: con contexto = t2, solo veo membership de t2
    async with session_scope() as s:
        await set_tenant_context(s, str(t2_id))
        result = await s.execute(select(Membership))
        memberships = result.scalars().all()
        assert len(memberships) == 1
        assert memberships[0].tenant_id == t2_id

    # Cleanup
    async with session_scope() as s:
        # Sin contexto RLS no puedo borrar. Necesito superuser o desactivar RLS.
        # Para tests, mejor usar un usuario que tenga BYPASSRLS, o limpiar con TRUNCATE.
        await s.execute(text("TRUNCATE memberships, users, tenants CASCADE"))
```

> **Nota sobre limpieza**: el usuario `saas` del docker compose no tiene `BYPASSRLS`. Para tests, mejor crear un usuario admin (`CREATE ROLE saas_test SUPERUSER`) o usar `SET session_replication_role = 'replica'` durante setup/teardown. Lo refinamos cuando hagamos fixtures de pytest. Para hoy, `TRUNCATE` con un usuario que tenga permisos es suficiente.

### `scripts/seed_dev.py`

```python
"""Seed de datos para desarrollo local.

Uso: uv run python scripts/seed_dev.py
"""
import asyncio

from sqlalchemy import select

from app.core.db import session_scope
from app.models import Membership, Tenant, User


async def main() -> None:
    async with session_scope() as s:
        # Tenant demo
        result = await s.execute(select(Tenant).where(Tenant.name == "Panadería Pepe"))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name="Panadería Pepe", plan="starter")
            s.add(tenant)
            await s.flush()

        # User demo
        result = await s.execute(select(User).where(User.email == "pepe@panaderia.com"))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(email="pepe@panaderia.com", name="Pepe")
            s.add(user)
            await s.flush()

        # Membership
        result = await s.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.tenant_id == tenant.id,
            )
        )
        if result.scalar_one_or_none() is None:
            s.add(Membership(user_id=user.id, tenant_id=tenant.id, role="admin"))

    print(f"✓ Seed completado. Tenant id: {tenant.id}")


if __name__ == "__main__":
    asyncio.run(main())
```

## Criterios de aceptación

- [x] `uv run alembic upgrade head` aplica las dos migraciones sin error.
- [x] `\dt` en psql muestra `tenants`, `users`, `memberships`, `alembic_version`.
- [x] `SELECT relrowsecurity FROM pg_class WHERE relname='memberships'` devuelve `t`.
- [x] `SELECT * FROM pg_policies WHERE tablename='memberships'` devuelve la política.
- [x] `uv run python scripts/seed_dev.py` crea tenant + user + membership.
- [x] `uv run pytest tests/integration/test_rls_isolation.py -v` pasa.
- [x] `uv run mypy app` pasa.
- [x] Commit hecho.

## Comandos útiles

```bash
# Generar migración
uv run alembic revision --autogenerate -m "descripción"

# Aplicar migraciones
uv run alembic upgrade head

# Bajar una migración
uv run alembic downgrade -1

# Ver historial
uv run alembic history

# Ver estado actual
uv run alembic current

# Seed
uv run python scripts/seed_dev.py

# Test específico
uv run pytest tests/integration/test_rls_isolation.py -v

# Reset completo de BD (¡cuidado!)
docker compose -f docker/docker-compose.yml down -v
rm -rf docker/data/postgres
./scripts/dev_up.sh
uv run alembic upgrade head
```

## Lo que NO toca este paso

- Modelos `Invoice`, `Document`, etc.: pasos posteriores.
- Conectar Clerk con `User`/`Tenant`: Paso 07.
- Middleware que aplica RLS en cada request: Paso 07.
- UI de gestión de organizaciones: Paso 08.

## Posibles problemas

**`alembic init` falla**: asegúrate de que el directorio `migrations/` no existe ya.

**Autogenerate no detecta los modelos**: confirma que importas los módulos en `migrations/env.py` (`from app.models import ...`). Si solo importas `Base`, los modelos no se registran.

**RLS bloquea el seed**: el usuario `saas` está sometido a RLS. Para scripts de seed que crean memberships, hay dos opciones: (a) hacer `SET LOCAL app.current_tenant = '<uuid>'` antes de insertar, (b) usar un usuario con `BYPASSRLS`. Para el seed inicial, lo más limpio es darle `BYPASSRLS` solo a operaciones del bootstrap.

**Test de aislamiento falla porque el cleanup deja datos**: usa `TRUNCATE ... CASCADE` o configura fixtures de pytest con rollback automático.

**Alembic genera ALTER columns no deseados** (typo en server_default, etc.): revisa la migración y elimina líneas que no quieras.

## Siguiente paso

`Paso07.md` — Integración con Clerk: middleware que valida JWT, provisioning automático de tenant/user/membership al primer login, y setear `app.current_tenant` en cada request.
