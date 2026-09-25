"""BD objetivo de los tests: siempre `saas_test`, nunca la BD de la app (`saas`).

Infisical `dev` inyecta DATABASE_URL apuntando a `saas`; los tests de
integración crean tenants, usuarios y documentos que no se limpian. Por eso
`tests/conftest.py` reescribe el nombre de BD de todas las URLs antes de
importar la app. Se conserva usuario, password, host y puerto.
"""

from collections.abc import Mapping

from sqlalchemy.engine import make_url

TEST_DATABASE_NAME = "saas_test"

_DEFAULT_APP_URL = (
    "postgresql+asyncpg://saas_app:saas@localhost:5432/saas"  # pragma: allowlist secret
)
_DEFAULT_RLS_URL = _DEFAULT_APP_URL


def to_test_database(url: str) -> str:
    """Devuelve la misma URL con la BD cambiada a `saas_test`."""
    return make_url(url).set(database=TEST_DATABASE_NAME).render_as_string(hide_password=False)


def is_test_database(url: str) -> bool:
    """True si la URL apunta a la BD de tests."""
    return make_url(url).database == TEST_DATABASE_NAME


def resolve_test_urls(env: Mapping[str, str]) -> tuple[str, str]:
    """(DATABASE_URL, RLS_TEST_DATABASE_URL) para tests, ambas en `saas_test`.

    Args:
        env: entorno del proceso (normalmente ``os.environ``).
    """
    app_url = env.get("DATABASE_URL") or _DEFAULT_APP_URL
    rls_url = env.get("RLS_TEST_DATABASE_URL") or _DEFAULT_RLS_URL
    return to_test_database(app_url), to_test_database(rls_url)


if __name__ == "__main__":
    # Usado por scripts/test_db_setup.sh para migrar saas_test.
    import os

    print(resolve_test_urls(os.environ)[0])
