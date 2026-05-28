from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


# Base declarativa compartida por todos los modelos ORM del proyecto.
# Al importar los modelos (via app/models/__init__.py), quedan registrados
# en Base.metadata, que Alembic inspecciona para generar migraciones.
class Base(DeclarativeBase):
    pass


# Singletons de motor y sessionmaker: son costosos de crear (el motor establece
# el pool de conexiones); se instancian una vez por proceso y se reutilizan.
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
# En Python 3.14 + Windows, asyncpg QueuePool.dispose() puede dejar el
# ProactorEventLoop en estado inválido. Los tests activan NullPool para evitarlo.
_use_null_pool: bool = False


def use_null_pool_for_tests() -> None:
    """Fuerza NullPool en el engine singleton para tests.

    En Python 3.14 + Windows, asyncpg QueuePool.dispose() puede dejar el
    ProactorEventLoop en estado inválido para conexiones posteriores en el mismo
    event loop. NullPool crea una conexión fresca por sesión y la cierra
    inmediatamente, evitando contaminación entre tests function-scoped.
    Llamar desde pytest_configure() para que aplique antes de cualquier test.
    """
    global _use_null_pool, _engine, _sessionmaker
    _use_null_pool = True
    _engine = None
    _sessionmaker = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        if _use_null_pool:
            from sqlalchemy.pool import NullPool

            _engine = create_async_engine(
                settings.database_url,
                echo=False,
                poolclass=NullPool,
            )
        else:
            _engine = create_async_engine(
                settings.database_url,
                # echo=False: deshabilita el log SQL en producción, que sería
                # extremadamente verboso (una línea por query en cada request).
                echo=False,
                # pool_size=10: conexiones persistentes en el pool. Con N workers
                # de uvicorn, el total de conexiones a Postgres = N x 10.
                pool_size=10,
                # max_overflow=20: conexiones adicionales permitidas cuando el pool
                # está lleno. Máximo total = pool_size + max_overflow = 30.
                max_overflow=20,
                # pool_pre_ping=True: antes de ceder una conexión del pool envía
                # un SELECT 1 para verificar que sigue activa. Previene errores de
                # "stale connection" tras reinicios de Postgres o timeouts de red.
                pool_pre_ping=True,
                # pool_recycle=3600: fuerza el reciclado de conexiones cada hora.
                # Algunos firewalls y load balancers cortan conexiones TCP inactivas
                # sin notificar; reciclar periódicamente evita ese problema.
                pool_recycle=3600,
            )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            # expire_on_commit=False: SQLAlchemy normalmente expira los
            # atributos de los objetos tras commit() para forzar recarga desde
            # BD. En sesiones async, esa recarga implícita falla porque la
            # conexión ya no está abierta. Con este flag los valores en memoria
            # se conservan sin necesitar otro roundtrip a BD.
            expire_on_commit=False,
            # autoflush=False: deshabilita el flush automático antes de cada
            # query. En código async con transacciones complejas, los flushes
            # implícitos pueden causar efectos inesperados; se prefiere control
            # explícito con await session.flush() cuando sea necesario.
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager con commit/rollback automático para uso fuera de FastAPI.

    Para jobs ARQ, scripts de administración o el eval runner, donde no existe
    el ciclo de vida de dependencias de FastAPI. Los handlers de FastAPI usan
    get_db() (en deps.py), que incluye también el contexto de tenant.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Cierra el pool de conexiones limpiamente durante el apagado de la app.

    Llamado en el lifespan de FastAPI (main.py). Sin esto, asyncpg puede
    registrar advertencias de "connection closed unexpectedly" al terminar
    el proceso, y las conexiones quedan abiertas en el lado de Postgres
    hasta que expira el timeout de inactividad.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    _sessionmaker = None


async def set_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    """Establece la variable de sesión Postgres que activa RLS para el tenant.

    Usa set_config con is_local=true (tercer argumento): la variable solo
    es válida dentro de la transacción actual y se revierte automáticamente
    al hacer rollback o commit. Esto impide que el contexto de un tenant
    "contamine" la conexión cuando el pool la reutilice para otra sesión.

    Se usa SELECT set_config() con parámetro enlazado (:tid) en lugar de
    `SET LOCAL app.current_tenant = '...'` para evitar inyección SQL incluso
    con valores internos de la app.
    """
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )


async def clear_tenant_context(session: AsyncSession) -> None:
    # Limpia el contexto de tenant, dejando current_setting() como cadena vacía.
    # Necesario en sesiones reutilizadas donde se quiere ejecutar una query
    # administrativa sin aislamiento por tenant (p. ej. tras un rollback que
    # revirtió un set_tenant_context previo en el mismo hilo de sesión).
    await session.execute(text("SELECT set_config('app.current_tenant', '', true)"))


@asynccontextmanager
async def session_factory_for_worker(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    """Sesión Postgres con contexto RLS de tenant para worker ARQ y eval runner.

    Equivalente a get_db() de deps.py pero sin el ciclo de vida de FastAPI:
    - No hay middleware de auth que setee request.state.
    - El commit/rollback lo gestiona el caller (invoice_jobs.py, eval runner).
    - Se aplica set_tenant_context inmediatamente para que RLS esté activo
      desde la primera query dentro del bloque `async with`.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        await set_tenant_context(session, str(tenant_id))
        # No hay commit automático: el caller decide cuándo y si hace commit.
        # Esto permite que el worker agrupe varias escrituras en una sola
        # transacción atómica (Invoice + InvoiceLines + LLMCall).
        yield session
