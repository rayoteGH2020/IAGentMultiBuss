import asyncio
import os
from logging.config import fileConfig

from alembic import context
from pydantic import ValidationError
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.models import Base

# IMPORTANTE: importar todos los modelos para que autogenerate los detecte
from app.models import (
    appointment,
    business_hour,
    invoice,
    llm_call,
    membership,
    professional,
    professional_specialty,
    professional_working_hour,
    schedule_exception,
    scheduling_service,
    tenant,
    user,
)  # noqa: F401


def _database_url() -> str:
    """URL de Postgres para migraciones.

    Prioridad:
    1. `DATABASE_URL` en el entorno (suficiente para Alembic).
    2. `get_settings().database_url` (requiere secretos vía Infisical).
    """
    if url := os.environ.get("DATABASE_URL"):
        return url
    try:
        from app.config import get_settings

        return get_settings().database_url
    except ValidationError as exc:
        msg = (
            "Alembic no puede conectar: falta DATABASE_URL (y Settings no está completo). "
            "Ejecuta: infisical run -- uv run alembic upgrade head"
        )
        raise RuntimeError(msg) from exc


config = context.config
_migration_url = _database_url()
config.set_main_option("sqlalchemy.url", _migration_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_migration_url,
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
        poolclass=pool.NullPool,
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
