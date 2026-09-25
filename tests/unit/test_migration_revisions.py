"""Static checks on Alembic revision scripts (no database required)."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# alembic_version.version_num is VARCHAR(32) by default.
_MAX_REVISION_LENGTH = 32

_ROOT = Path(__file__).resolve().parents[2]


def _script_directory() -> ScriptDirectory:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def test_revision_ids_fit_alembic_version_column() -> None:
    too_long = [
        script.revision
        for script in _script_directory().walk_revisions()
        if len(script.revision) > _MAX_REVISION_LENGTH
    ]
    assert too_long == []


def test_single_head() -> None:
    assert len(_script_directory().get_heads()) == 1
