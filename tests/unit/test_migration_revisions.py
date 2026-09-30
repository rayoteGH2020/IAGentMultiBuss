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


def test_llm_budget_migration_matches_plan_seed() -> None:
    """p76 escribe en BD los mismos presupuestos que el seed ``PLAN_LIMITS`` (D026)."""
    from decimal import Decimal

    from app.core.entitlement_codes import LIMIT_LLM_BUDGET_EUR_MONTH, PLAN_LIMITS

    script = _script_directory().get_revision("p76_llm_budget_01")
    assert script is not None
    new_values: dict[str, int] = script.module._NEW
    assert {code: PLAN_LIMITS[code][LIMIT_LLM_BUDGET_EUR_MONTH] for code in new_values} == {
        code: Decimal(value) for code, value in new_values.items()
    }
    assert set(new_values) == set(PLAN_LIMITS)
