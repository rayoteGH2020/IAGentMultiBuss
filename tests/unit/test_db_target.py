"""Los tests nunca apuntan a la BD de la app (`saas`), solo a `saas_test`."""

import os

from sqlalchemy.engine import make_url

from tests.db_target import (
    TEST_DATABASE_NAME,
    is_test_database,
    resolve_test_urls,
    to_test_database,
)

_APP_URL = "postgresql+asyncpg://saas:pw@localhost:5432/saas"  # pragma: allowlist secret
_RLS_URL = "postgresql+asyncpg://saas_app:pw2@db.local:6543/saas"  # pragma: allowlist secret


def test_to_test_database_only_changes_database_name() -> None:
    result = make_url(to_test_database(_APP_URL))
    assert result.database == TEST_DATABASE_NAME
    assert result.username == "saas"
    assert result.password == "pw"  # pragma: allowlist secret
    assert result.host == "localhost"
    assert result.port == 5432
    assert result.drivername == "postgresql+asyncpg"


def test_resolve_rewrites_both_urls_from_infisical_env() -> None:
    app_url, rls_url = resolve_test_urls(
        {"DATABASE_URL": _APP_URL, "RLS_TEST_DATABASE_URL": _RLS_URL}
    )
    assert is_test_database(app_url)
    assert is_test_database(rls_url)
    assert make_url(rls_url).host == "db.local"
    assert make_url(rls_url).username == "saas_app"


def test_resolve_defaults_without_env_use_rls_role() -> None:
    app_url, rls_url = resolve_test_urls({})
    assert is_test_database(app_url)
    assert make_url(rls_url).username == "saas_app"


def test_resolve_is_idempotent_for_ci_urls() -> None:
    ci_url = to_test_database(_APP_URL)
    assert resolve_test_urls({"DATABASE_URL": ci_url})[0] == ci_url


def test_app_database_is_not_a_test_database() -> None:
    assert not is_test_database(_APP_URL)


def test_pytest_session_points_to_test_database() -> None:
    # pytest_configure (tests/conftest.py) ya reescribió el entorno.
    assert is_test_database(os.environ["DATABASE_URL"])
    assert is_test_database(os.environ["RLS_TEST_DATABASE_URL"])
