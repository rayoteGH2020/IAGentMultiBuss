"""BD de los evals: siempre `saas_test`, nunca la BD de la app (`saas`).

Infisical `dev` inyecta DATABASE_URL apuntando a `saas`; los runners de evals
crean tenant, llm_calls, documentos sembrados y conocimiento que no se limpian.
Comparten `saas_test` con pytest (tests/db_target.py): los evals usan un tenant
fijo (EVAL_TENANT_ID) y los tests tenants aleatorios, así que no se pisan.
Cada punto de entrada que toca BD llama a ``use_eval_database()`` antes de
crear el engine; se conserva usuario, password, host y puerto.

Crear/migrar la BD: ``infisical run -- bash scripts/test_db_setup.sh``.
"""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url

EVAL_DATABASE_NAME = "saas_test"


def to_eval_database(url: str) -> str:
    """Devuelve la misma URL con la BD cambiada a `saas_test`."""
    return make_url(url).set(database=EVAL_DATABASE_NAME).render_as_string(hide_password=False)


def use_eval_database() -> str:
    """Redirige DATABASE_URL del proceso a `saas_test` y devuelve la URL nueva.

    Debe llamarse antes de crear el engine (primera línea de ``main()``).

    Raises:
        SystemExit: si falta DATABASE_URL o el engine ya se creó con otra BD.
    """
    current = os.environ.get("DATABASE_URL")
    if not current:
        msg = "DATABASE_URL no definida: ejecutar con `infisical run -- ...`"
        raise SystemExit(msg)
    from app.core import db

    if db._engine is not None and db._engine.url.database != EVAL_DATABASE_NAME:
        msg = "El engine de BD ya existe: use_eval_database() debe llamarse antes"
        raise SystemExit(msg)
    eval_url = to_eval_database(current)
    os.environ["DATABASE_URL"] = eval_url
    from app.config import get_settings

    get_settings.cache_clear()
    return eval_url
