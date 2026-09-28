"""Tests de la redirección de los evals a la BD `saas_test`."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from app.config import get_settings
from app.evals import eval_db
from app.evals.eval_db import EVAL_DATABASE_NAME, to_eval_database, use_eval_database

_APP_URL = "postgresql+asyncpg://saas:secret@db.local:6543/saas"  # pragma: allowlist secret


@pytest.fixture
def clean_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Sin engine previo (otros tests lo crean contra saas_test) y caché limpia al terminar."""
    from app.core import db

    monkeypatch.setattr(db, "_engine", None)
    yield
    get_settings.cache_clear()


def test_to_eval_database_keeps_credentials_host_and_port() -> None:
    expected = (
        "postgresql+asyncpg://saas:secret@db.local:6543/saas_test"  # pragma: allowlist secret
    )

    assert to_eval_database(_APP_URL) == expected


def test_use_eval_database_rewrites_env(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: None,
) -> None:
    monkeypatch.setenv("DATABASE_URL", _APP_URL)

    url = use_eval_database()

    assert url.endswith(f"/{EVAL_DATABASE_NAME}")
    assert eval_db.os.environ["DATABASE_URL"] == url
    assert use_eval_database() == url


def test_use_eval_database_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SystemExit, match="DATABASE_URL"):
        use_eval_database()


def test_use_eval_database_refuses_existing_engine_on_other_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import db

    engine = MagicMock()
    engine.url.database = "saas"
    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setenv("DATABASE_URL", _APP_URL)

    with pytest.raises(SystemExit, match="engine"):
        use_eval_database()


def test_chat_documents_validate_only_does_not_need_database(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.evals.runners import chat_documents

    called = MagicMock()
    monkeypatch.setattr(chat_documents, "use_eval_database", called)
    monkeypatch.setattr(sys, "argv", ["chat_documents", "--validate-only"])

    chat_documents.main()

    called.assert_not_called()
    assert '"valid": true' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("module_name", "argv"),
    [
        ("app.evals.runners.extraction", ["extraction"]),
        ("app.evals.runners.document_extraction", ["document_extraction", "ticket"]),
        ("app.evals.runners.chat_documents", ["chat_documents"]),
        ("app.evals.runners.knowledge_qa", ["knowledge_qa"]),
        ("app.evals.runners.knowledge_retrieval", ["knowledge_retrieval"]),
        ("app.evals.seed_knowledge_eval", ["seed_knowledge_eval", "--list-tenants"]),
        ("app.evals.seed_documents_eval", ["seed_documents_eval"]),
    ],
)
def test_live_entry_points_switch_database_first(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    argv: list[str],
) -> None:
    """La redirección ocurre antes de cualquier trabajo: se corta ahí con SystemExit."""
    import importlib

    module = importlib.import_module(module_name)
    stop = MagicMock(side_effect=SystemExit("eval db switched"))
    monkeypatch.setattr(module, "use_eval_database", stop)
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit, match="eval db switched"):
        module.main()

    stop.assert_called_once_with()
