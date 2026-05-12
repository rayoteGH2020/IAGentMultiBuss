# Paso 01 — Bootstrap del proyecto

## Objetivo

Crear el esqueleto del repositorio con la estructura de carpetas definitiva, gestor de paquetes `uv` instalado, `pyproject.toml` con todas las dependencias raíz, y tooling (`ruff`, `mypy`, `pre-commit`) funcionando en local.

Al final de este paso, `uv run python -c "import app"` debe funcionar y `ruff check .` debe pasar.

## Pre-requisitos

- Python 3.12+ instalado.
- `uv` instalado (ver sección "Comandos útiles" si no).
- Git inicializado en el directorio del proyecto.

## Contexto relevante

- `arquitectura.md` sección 3 (Stack) y sección 4 (Estructura del repositorio).
- `Agents.md` sección 1 (Stack obligatorio), **§2 (Infisical, sin `.env`)** y sección 4 (Convenciones Python).

## Tareas

- [ ] Crear directorio raíz del proyecto y `cd` dentro.
- [ ] Inicializar repositorio Git con `.gitignore` apropiado.
- [ ] Inicializar proyecto con `uv init --python 3.12`.
- [ ] Crear estructura de carpetas vacía con `__init__.py` donde corresponda.
- [ ] Configurar `pyproject.toml` con dependencias, ruff, mypy.
- [ ] Instalar dependencias con `uv sync`.
- [ ] Configurar `pre-commit` con hooks de ruff y mypy.
- [ ] Crear `docs/environment-variables.md` con el **listado de nombres** de variables para registrar en **Infisical** (MAYÚSCULAS). No crear `.env` ni `.env.example` (ver `Agents.md` §2).
- [ ] Crear README.md mínimo.
- [ ] Verificar que `uv run python -c "import app"` funciona.
- [ ] Verificar que `uv run ruff check .` y `uv run mypy app` pasan (aunque sea sin código real aún).
- [ ] Primer commit: `chore: project scaffolding`.

## Detalles técnicos

### Estructura inicial a crear

```
mi-saas/
├── app/
│   ├── __init__.py
│   ├── main.py                # vacío de momento, se completa en Paso 03
│   ├── config.py              # vacío
│   ├── deps.py                # vacío
│   ├── core/
│   │   └── __init__.py
│   ├── models/
│   │   └── __init__.py
│   ├── schemas/
│   │   └── __init__.py
│   ├── llm/
│   │   ├── __init__.py
│   │   └── prompts/
│   │       └── .gitkeep
│   ├── services/
│   │   └── __init__.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── web/
│   │   │   └── __init__.py
│   │   └── api/
│   │       └── __init__.py
│   ├── templates/
│   │   └── .gitkeep
│   ├── static/
│   │   ├── css/
│   │   │   └── .gitkeep
│   │   ├── js/
│   │   │   └── .gitkeep
│   │   └── img/
│   │       └── .gitkeep
│   ├── jobs/
│   │   └── __init__.py
│   └── evals/
│       ├── __init__.py
│       ├── datasets/
│       │   └── .gitkeep
│       └── runners/
│           └── __init__.py
├── migrations/                # se inicializa en Paso 06
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   └── __init__.py
│   ├── integration/
│   │   └── __init__.py
│   └── e2e/
│       └── __init__.py
├── docker/                    # se completa en Paso 02
├── scripts/
├── docs/
│   └── environment-variables.md  # nombres de variables para Infisical (sin secretos en Git)
├── .github/
│   └── workflows/
├── .gitignore
├── pyproject.toml
├── README.md
├── arquitectura.md            # copiar el existente
├── AGENTS.md                  # copiar el existente
└── instrucciones-asistente.md # copiar el existente
```

### `.gitignore`

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.python-version

# uv
.uv/

# Entornos (Infisical inyecta variables; ignorar `.env*` si se crean por error)
.env
.env.local
.env.*.local

# IDE
.vscode/
.idea/
*.swp
.DS_Store

# Tests / coverage
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/
.ruff_cache/

# Tailwind compilado
app/static/css/app.css

# Logs
*.log
logs/

# Datos locales
data/
*.db
*.sqlite

# Build / dist
dist/
build/

# Playwright
test-results/
playwright-report/
playwright/.cache/
```

### `pyproject.toml`

```toml
[project]
name = "mi-saas"
version = "0.1.0"
description = "SaaS modular para pymes"
requires-python = ">=3.12"
dependencies = [
    # Web
    "fastapi[standard]>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "jinja2>=3.1.4",
    "python-multipart>=0.0.12",
    "httpx>=0.27.2",

    # Datos
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "alembic>=1.14.0",
    "redis>=5.2.0",
    "pgvector>=0.3.6",

    # Validación y settings
    "pydantic>=2.10.0",
    "pydantic-settings>=2.6.0",

    # LLM
    "anthropic>=0.40.0",
    "google-genai>=0.3.0",
    "instructor>=1.7.0",
    "voyageai>=0.3.0",

    # Background jobs
    "arq>=0.26.0",

    # Storage
    "boto3>=1.35.0",

    # Observability
    "structlog>=24.4.0",
    "langfuse>=2.55.0",

    # Auth
    "pyjwt[crypto]>=2.10.0",
    "cryptography>=43.0.0",

    # Webhooks
    "svix>=1.42.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=6.0.0",
    "ruff>=0.7.0",
    "mypy>=1.13.0",
    "pre-commit>=4.0.0",
    "playwright>=1.48.0",
    "testcontainers[postgres]>=4.8.0",
    "factory-boy>=3.3.0",
    "respx>=0.21.0",
    # Types stubs
    "types-boto3>=1.35.0",
]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["app", "tests"]
exclude = ["migrations/versions"]

[tool.ruff.lint]
select = [
    "E", "F", "W",      # pyflakes + pycodestyle
    "I",                 # isort
    "B",                 # bugbear
    "C4",                # comprehensions
    "UP",                # pyupgrade
    "SIM",               # simplify
    "RUF",               # ruff-specific
    "TCH",               # type-checking imports
    "ASYNC",             # async lints
    "S",                 # bandit (security)
]
ignore = [
    "E501",              # line too long (formatter handles it)
    "S101",              # assert in tests
    "B008",              # function call as default arg (FastAPI Depends)
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S", "ASYNC"]
"migrations/**" = ["ALL"]

[tool.ruff.format]
quote-style = "double"
docstring-code-format = true

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
exclude = ["migrations/versions", "tests/"]

[[tool.mypy.overrides]]
module = ["arq.*", "voyageai.*", "instructor.*", "langfuse.*", "svix.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra --strict-markers --tb=short"
markers = [
    "integration: requiere infra (postgres, redis)",
    "e2e: requiere navegador",
    "slow: tarda más de 1s",
]

[tool.pydantic-mypy]
init_forbid_extra = true
init_typed = true
warn_required_dynamic_aliases = true
```

### `docs/environment-variables.md`

Documento de referencia en el repo: **solo nombres** y descripciones breves. Los valores reales (secretos, URLs con credenciales) viven en **Infisical**; arranque local con `infisical run -- ...` (ver `Agents.md` §2).

Incluye al menos estas claves en **MAYÚSCULAS** (coinciden con lo que leerá `pydantic-settings` vía entorno). Ejemplo de contenido del fichero:

```text
# Variables de entorno (Infisical)

| Variable | Uso |
|----------|-----|
| APP_ENV | development / staging / production |
| APP_SECRET_KEY | Secreto de sesión / firmas internas |
| APP_BASE_URL | URL pública de la app |
| LOG_LEVEL | DEBUG, INFO, … |
| DATABASE_URL | Postgres async (postgresql+asyncpg://…) |
| REDIS_URL | Redis (redis://localhost:6379/0) |
| R2_* | Cloudflare R2 |
| CLERK_* | Clerk (auth) |
| ANTHROPIC_API_KEY, GOOGLE_API_KEY, VOYAGE_API_KEY | Proveedores LLM |
| LANGFUSE_* | Langfuse |
| ENCRYPTION_KEY | Cifrado de campos sensibles |

Ejemplos no secretos para dev local (Paso 02), solo guía al rellenar Infisical:
- DATABASE_URL=postgresql+asyncpg://saas:saas@localhost:5432/saas
- REDIS_URL=redis://localhost:6379/0
- LANGFUSE_HOST=http://localhost:3000
```

*(Ajusta la tabla si añades variables en pasos posteriores.)*

### `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.7.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0
    hooks:
      - id: mypy
        additional_dependencies:
          - pydantic
          - sqlalchemy
          - types-boto3

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: check-yaml
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: detect-private-key

  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
```

### `README.md` mínimo

```markdown
# Mi SaaS

SaaS modular para pymes (gestorías, peluquerías, talleres, etc.) con tres módulos:

1. Extracción y conciliación administrativa (facturas, tickets).
2. Agente RAG conversacional (WhatsApp, web).
3. Analista de datos conversacional.

## Documentación

- `arquitectura.md` — Arquitectura del sistema.
- `AGENTS.md` — Reglas para el asistente de IA.
- `instrucciones-asistente.md` — Cómo usar Cursor / Claude Code.
- `Paso0X.md` — Pasos de construcción.

## Desarrollo local

Ver `Paso01.md` para el bootstrap inicial.

\`\`\`bash
uv sync
infisical run -- uv run uvicorn app.main:app --reload
\`\`\`

*(Tras el Paso 03; hasta entonces la app puede no existir aún. Secretos vía Infisical, ver `Agents.md` §2.)*

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 · Jinja2 + HTMX + Alpine.js + Tailwind 4 · Postgres + pgvector · Redis · Cloudflare R2 · Anthropic + Google Gen AI · Clerk · Langfuse.
```

## Criterios de aceptación

- [ ] `uv sync` completa sin errores.
- [ ] `uv run python -c "import app"` no falla.
- [ ] `uv run ruff check .` pasa.
- [ ] `uv run mypy app` pasa (con código vacío).
- [ ] `pre-commit run --all-files` pasa al menos los hooks de ruff y formato.
- [ ] Ningún fichero `.env` ni secretos en el repositorio; `.gitignore` ignora patrones típicos (`.env`, `.env.local`, …) por si se crean por error.
- [ ] Primer commit hecho con mensaje `chore: project scaffolding`.

## Comandos útiles

```bash
# Instalar uv (si no lo tienes)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Crear proyecto
mkdir mi-saas && cd mi-saas
git init
uv init --python 3.12 --no-readme

# Tras crear pyproject.toml manualmente:
uv sync

# Instalar pre-commit hooks
uv run pre-commit install

# Tooling
uv run ruff check .
uv run ruff format .
uv run mypy app
uv run pytest

# Arranque con secretos (después de configurar Infisical CLI)
infisical run -- uv run uvicorn app.main:app --reload

# Generar APP_SECRET_KEY o ENCRYPTION_KEY
openssl rand -hex 32
openssl rand -base64 32
```

## Lo que NO toca este paso

- Levantar servicios (Postgres, Redis): es el Paso 02.
- Código de FastAPI: es el Paso 03.
- Frontend (HTMX, Tailwind): es el Paso 04.
- Modelos de BD: empieza en el Paso 06.

## Posibles problemas

**`uv sync` falla en alguna dependencia**: si una librería del listado tiene versión nueva y rompe la resolución, prueba a quitar pins estrictos (`>=X.Y.0`). Si una librería de IA cambia mucho, fija la versión exacta tras probar que va.

**`mypy` se queja de imports sin tipos**: añade el módulo al bloque `[[tool.mypy.overrides]]` con `ignore_missing_imports = true`.

**`pre-commit` tarda mucho la primera vez**: normal, descarga entornos. Las siguientes son rápidas.

## Siguiente paso

`Paso02.md` — Docker Compose con Postgres + pgvector, Redis y Langfuse para desarrollo local.
