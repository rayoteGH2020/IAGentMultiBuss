"""Static checks on production deploy files (Dockerfile, compose, Caddy, scripts).

No Docker required: parses the files and asserts the security/ops invariants
documented in Documentacion_V2/Paso11_Despliegue_VPS.md.
"""

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from app.config import Settings

_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY = _ROOT / "deploy"
_COMPOSE = _DEPLOY / "docker-compose.prod.yml"
_APP_SERVICES = ("api", "worker")
_INTERNAL_SERVICES = ("api", "worker", "postgres", "redis")
_SHELL_SCRIPTS = sorted(
    [*(_DEPLOY / "scripts").glob("*.sh"), _DEPLOY / "postgres" / "02-app-role.sh"]
)


def _compose() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    return data


def _env_names(service: dict[str, Any]) -> set[str]:
    env = service.get("environment", [])
    if isinstance(env, dict):
        return set(env)
    return {entry.split("=", 1)[0] for entry in env}


def _settings_env_names() -> set[str]:
    # email_sadm usa validation_alias, pero EMAIL_SADM coincide con name.upper().
    return {name.upper() for name in Settings.model_fields}


def _load_healthcheck() -> ModuleType:
    spec = importlib.util.spec_from_file_location("deploy_healthcheck", _DEPLOY / "healthcheck.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Compose ─────────────────────────────────────────────────────────────────


def test_compose_defines_expected_services() -> None:
    assert set(_compose()["services"]) == {"caddy", "api", "worker", "postgres", "redis"}


@pytest.mark.parametrize("service_name", _APP_SERVICES)
def test_app_services_receive_every_settings_variable(service_name: str) -> None:
    service = _compose()["services"][service_name]
    missing = _settings_env_names() - _env_names(service)
    assert missing == set(), f"añadir a x-app-env en docker-compose.prod.yml: {sorted(missing)}"


@pytest.mark.parametrize("service_name", _APP_SERVICES)
def test_app_env_is_passthrough_only(service_name: str) -> None:
    # Valores solo desde Infisical: ninguna entrada con valor literal.
    env = _compose()["services"][service_name]["environment"]
    assert all("=" not in entry for entry in env)


def test_no_service_uses_env_file() -> None:
    for name, service in _compose()["services"].items():
        assert "env_file" not in service, f"{name} usa env_file (prohibido, Agents.md §2)"


def test_only_caddy_publishes_ports() -> None:
    services = _compose()["services"]
    for name in _INTERNAL_SERVICES:
        assert "ports" not in services[name], f"{name} no debe publicar puertos"
    published = {str(port).split(":")[0] for port in services["caddy"]["ports"]}
    assert published == {"80", "443"}


def test_api_trusts_proxy_headers() -> None:
    command = _compose()["services"]["api"]["command"]
    assert "--proxy-headers" in command
    assert "app.main:app" in command


def test_app_containers_are_hardened() -> None:
    services = _compose()["services"]
    for name in _APP_SERVICES:
        assert services[name]["read_only"] is True
        assert "no-new-privileges:true" in services[name]["security_opt"]


def test_worker_has_its_own_healthcheck() -> None:
    test = _compose()["services"]["worker"]["healthcheck"]["test"]
    assert "--check" in test


def test_redis_never_evicts_queue_keys() -> None:
    command = " ".join(_compose()["services"]["redis"]["command"])
    assert "--maxmemory-policy noeviction" in command
    assert "--requirepass" in command
    assert "--appendonly yes" in command


def test_postgres_creates_app_role_before_migrations() -> None:
    volumes = _compose()["services"]["postgres"]["volumes"]
    assert any(v.endswith("/docker-entrypoint-initdb.d/02-app-role.sh:ro") for v in volumes)
    assert "SAAS_APP_DB_PASSWORD" in _env_names(_compose()["services"]["postgres"])


def test_app_role_script_disables_rls_bypass() -> None:
    sql = (_DEPLOY / "postgres" / "02-app-role.sh").read_text(encoding="utf-8")
    assert "NOBYPASSRLS" in sql
    assert "NOSUPERUSER" in sql
    assert ":'app_password'" in sql  # contraseña por variable psql, no interpolada


# ── Dockerfile / contexto de build ──────────────────────────────────────────


def test_dockerfile_runs_as_non_root() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    user_lines = [line.split()[1] for line in dockerfile.splitlines() if line.startswith("USER ")]
    assert user_lines, "falta USER en el Dockerfile"
    assert user_lines[-1] not in {"root", "0"}


def test_dockerfile_pins_tailwind_checksum() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"ADD --checksum=sha256:[0-9a-f]{64}", dockerfile)


def test_dockerfile_does_not_bake_secrets() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert ".env" not in dockerfile
    assert not re.search(r"^(ENV|ARG)\s+\S*(SECRET|PASSWORD|API_KEY|TOKEN)", dockerfile, re.M)


@pytest.mark.parametrize("pattern", [".env", ".env.*", ".infisical.json", ".git", "docker/data"])
def test_dockerignore_excludes_sensitive_paths(pattern: str) -> None:
    lines = (_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert pattern in lines


# ── Caddy ───────────────────────────────────────────────────────────────────


def test_caddyfile_proxies_to_api_and_hides_metrics() -> None:
    caddyfile = (_DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "reverse_proxy api:8000" in caddyfile
    assert "respond @internal 404" in caddyfile
    assert "max_size 20MB" in caddyfile
    assert "trusted_proxies" not in caddyfile


# ── Healthcheck ─────────────────────────────────────────────────────────────


def test_healthcheck_headers_mimic_caddy() -> None:
    healthcheck = _load_healthcheck()
    headers = healthcheck.build_headers("https://app.example.com")
    assert headers == {"X-Forwarded-Proto": "https", "Host": "app.example.com"}


def test_healthcheck_headers_without_base_url() -> None:
    healthcheck = _load_healthcheck()
    assert healthcheck.build_headers("") == {"X-Forwarded-Proto": "https"}


def test_healthcheck_fails_closed_when_api_is_down() -> None:
    healthcheck = _load_healthcheck()
    healthcheck.HEALTH_URL = "http://127.0.0.1:9/health"  # puerto discard: sin servidor
    assert healthcheck.check("https://app.example.com") is False


# ── Scripts ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("script", _SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_use_lf_line_endings(script: Path) -> None:
    assert b"\r\n" not in script.read_bytes()


@pytest.mark.parametrize("script", _SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_parse(script: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash no disponible")
    result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_restore_requires_explicit_confirmation() -> None:
    script = (_DEPLOY / "scripts" / "restore.sh").read_text(encoding="utf-8")
    assert "--yes-destroy-data" in script


def test_deploy_migrates_with_owner_role_not_app_role() -> None:
    script = (_DEPLOY / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert 'DATABASE_URL="$MIGRATIONS_DATABASE_URL"' in script
    assert "backup.sh" in script
