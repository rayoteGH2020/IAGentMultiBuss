# CorreccionesCDX — Plan de implementación

> Documento de trabajo para corregir **5 hallazgos** del análisis externo (CDX).
> **Estado:** pendiente de implementación — usar este fichero como checklist cuando se ejecute el trabajo.
> **Referencia cruzada:** `Documentacion/Todo202607.md` (backlog general), `Documentacion/PendienteImplementar.md` §3 bis (multi-IVA).

---

## Cómo usar este documento

1. Implementar **un punto a la vez** (orden recomendado abajo).
2. Tras cada punto, ejecutar su sección **Comprobaciones finales** antes de pasar al siguiente.
3. Marcar `[x]` en las casillas de verificación al cerrar cada punto.
4. No mezclar el punto 5 (multi-IVA) con los puntos 1–4 salvo que se quiera un PR grande; es el más amplio.

**Orden recomendado:** 1 → 3 → 4 → 2 → 5
(Razón: arreglar calidad estática y CI primero; seguridad webhooks antes de refactor de capas; multi-IVA al final por alcance.)

---

## Punto 1 — Ruff y mypy en rojo

> **Estado:** implementado (2026-06-30).

### Problema detectado

- **Ruff** falla en `app/config.py`, `app/routes/api/webhooks_telegram.py` y `tests/unit/test_calendar_tools.py` (whitespace, imports, formato).
- **mypy --strict** falla en `app/llm/client.py`: `knowledge_embedding_model` es `str | None` en settings pero se usa como `str` en `embed()`.

### Por qué importa

- Los hooks `pre-commit` y la futura CI (punto 3) bloquearán merges.
- Con `knowledge_embedding_model=None` (default actual), el embedder puede recibir `model=None` en runtime → error en Voyage o coste mal registrado en `llm_calls`.

### Ficheros afectados

| Fichero | Tipo de cambio |
|---------|----------------|
| `app/config.py` | Limpieza ruff (trailing whitespace L78, comentario L98) |
| `app/routes/api/webhooks_telegram.py` | Imports al tope; opcional `structlog` |
| `tests/unit/test_calendar_tools.py` | Formato / orden imports (ruff) |
| `app/llm/client.py` | Fallback de modelo de embeddings |

### Cambios detallados

#### 1.1 — `app/config.py`

- [x] Eliminar espacios en blanco en línea vacía tras `langfuse_host` (~L78).
- [x] Corregir L98: comentario en línea anterior; sin `#` inline pegado al valor.
- [x] Revisar que no queden líneas con trailing whitespace en todo el fichero.
- [x] Property `resolved_knowledge_embedding_model` añadida.

**Opción preferida (también arregla mypy en origen):** añadir propiedad o default explícito:

```python
# Opción A — property en Settings (recomendada para mypy en todo el proyecto):
@property
def resolved_knowledge_embedding_model(self) -> str:
    return self.knowledge_embedding_model or "voyage-3-lite"
```

Si se usa la property, documentar en comentario que `KNOWLEDGE_EMBEDDING_MODEL` en Infisical override el default.

#### 1.2 — `app/llm/client.py` — método `embed()`

- [x] En `embed()`, resolver el modelo **una sola vez** vía `resolved_knowledge_embedding_model`.
- [x] Usar esa variable `model` (tipo `str`) en VoyageEmbedder, Langfuse y `compute_cost_eur`.
- [x] No duplicar lectura de settings sin fallback.

#### 1.3 — `app/routes/api/webhooks_telegram.py`

- [x] Mover imports de dentro del handler al tope del fichero.
- [x] Sustituir `logging` por `structlog`.
- [x] `UUID` en bloque `TYPE_CHECKING` (compatible con `from __future__ import annotations`).
- [x] `ruff format` aplicado.

#### 1.4 — `tests/unit/test_calendar_tools.py`

- [x] Imports ordenados; eliminado `MagicMock` sin uso.
- [x] `pytest.raises(ValidationError)` en lugar de `Exception` (B017).

### Tests a tocar

- [x] `tests/unit/test_calendar_tools.py` — 18 tests verdes.
- [ ] `tests/integration/test_llm_client.py` — requiere Postgres + Infisical (no ejecutado en este entorno).

### Comprobaciones finales (punto 1)

```powershell
# Desde la raíz del repo
uv run ruff check app/config.py app/llm/client.py app/routes/api/webhooks_telegram.py tests/unit/test_calendar_tools.py
uv run ruff format --check app/config.py app/llm/client.py app/routes/api/webhooks_telegram.py tests/unit/test_calendar_tools.py
uv run mypy app/llm/client.py app/config.py
uv run pytest tests/unit/test_calendar_tools.py -q
# test_llm_client es integración + real_llm: solo con Postgres levantado e Infisical:
# infisical run -- uv run pytest tests/integration/test_llm_client.py -q -m real_llm
pre-commit run --files app/config.py app/llm/client.py app/routes/api/webhooks_telegram.py tests/unit/test_calendar_tools.py
```

**Criterio de cierre:** ruff, mypy y `test_calendar_tools` verdes; `test_llm_client` skip (sin Postgres/clave) o pass (con Infisical + docker).

---

## Punto 2 — Regla de capas: `routes/` no debe importar `models/`

### Problema detectado

`Agents.md` §3: **`routes/` no importa `models/` directamente** — siempre vía `services/`.

Importaciones actuales (no exhaustivo):

| Fichero | Import directo de models | Gravedad |
|---------|--------------------------|----------|
| `app/routes/web/knowledge.py` | `KnowledgeDocument`, enums + **query SQLAlchemy en la ruta** (FAQ edit ~L324-335) | Alta |
| `app/routes/api/metrics.py` | `Invoice`, `LLMCall` | Media (excepción documentada en el propio módulo) |
| `app/routes/web/admin_channel_integrations.py` | `Tenant`, `ChannelIntegrationStatus` | Media |
| `app/routes/web/calendar.py` | `CalendarIntegrationStatus` | Baja (enum UI) |
| `app/routes/web/integrations.py` | `CalendarIntegrationStatus` | Baja |
| `app/routes/web/calendar_voice.py` | `CalendarIntegrationStatus` | Baja |

### Por qué importa

- Acopla HTTP a SQLAlchemy ORM; cambios en modelos rompen rutas.
- `knowledge.py` mezcla **acceso a BD en la ruta** (anti-patrón más grave que importar un enum).
- Inconsistencia: `metrics.py` ya documenta una excepción; el resto no.

### Estrategia acordada

1. **Corregir violaciones graves** (queries en routes).
2. **Mover enums** usados en templates a `app/schemas/` (o `app/schemas/enums.py`).
3. **Centralizar excepciones** documentadas en `Agents.md` §3 (lista cerrada).
4. **No** reescribir todo el proyecto de golpe: fases.

### Ficheros afectados (implementación)

| Acción | Ficheros |
|--------|----------|
| Mover query FAQ edit a servicio | `knowledge.py`, `knowledge_document_service.py` |
| Enums a schemas | Nuevo o existente `app/schemas/knowledge.py`, `app/schemas/calendar.py`, `app/schemas/channel.py` |
| Métricas cross-tenant | Nuevo `app/services/metrics_service.py` + simplificar `metrics.py` |
| Admin integraciones | `admin_channel_integrations.py` + servicio admin |
| Documentación | `Agents.md` o `Documentacion/arquitectura.md` §3 |

### Cambios detallados

#### 2.1 — Prioridad alta: `knowledge.py` FAQ edit

**Situación actual:** `knowledge_faq_edit` hace `select(KnowledgeDocument)...` dentro de la ruta.

**Cambio:**

- [ ] Añadir en `knowledge_document_service.py` algo equivalente a:

```python
async def get_document_orm(
    db: AsyncSession, *, tenant_id: UUID, document_id: UUID
) -> KnowledgeDocument:
    """Devuelve ORM para templates que aún necesitan el modelo (FAQ edit)."""
```

  **O mejor (preferido):** usar el `get_document()` existente que ya devuelve `KnowledgeDocumentRead` y adaptar el template `knowledge_faq_edit_panel.html` para no necesitar ORM.

- [ ] Eliminar de `knowledge.py`:
  - import de `KnowledgeDocument` (si ya no hace falta)
  - bloque `sa_select(KnowledgeDocument)...`
  - import inline `from sqlalchemy import select`

- [ ] Mantener en ruta solo: llamada a servicio + `render()`.

#### 2.2 — Enums para templates (prioridad media)

- [ ] Crear literales/enums en schemas, p. ej.:
  - `KnowledgeDocumentKind`, `KnowledgeDocumentStatus` → re-export desde `app/schemas/knowledge.py` (o mover desde models y dejar models con String + check constraint — **decidir una sola fuente**).
  - `CalendarIntegrationStatus` → `app/schemas/calendar.py`
  - `ChannelIntegrationStatus` → `app/schemas/channel.py`

- [ ] Actualizar imports en:
  - `knowledge.py` (listas `kinds`/`statuses` en contexto Jinja)
  - `calendar.py`, `integrations.py`, `calendar_voice.py`
  - `admin_channel_integrations.py`

- [ ] Models pueden importar los mismos enums desde schemas **o** mantener valores string en BD; evitar duplicar definiciones.

#### 2.3 — `metrics.py` (prioridad media)

**Situación:** agregaciones SQL cross-tenant; comentario L7-10 justifica excepción.

**Cambio (elegir A o B):**

- **A (preferida):** crear `app/services/metrics_service.py` con funciones `get_module1_metrics(db) -> dict` que contengan los `select()` sobre `Invoice` y `LLMCall`. La ruta solo valida token y devuelve JSON/HTML.

- **B (mínima):** dejar lógica en ruta pero añadir en `Agents.md` §3:

  > Excepciones permitidas a importar `models/`: `routes/api/metrics.py` (agregaciones cross-tenant read-only).

#### 2.4 — `admin_channel_integrations.py`

- [ ] Devolver desde servicio DTOs/schemas (`TenantRead`, `ChannelIntegrationRead`) en lugar de pasar ORM `Tenant` al template.
- [ ] Mover queries a `channel_integration_service` o `admin_service`.

### Tests a tocar / crear

- [ ] `tests/integration/test_knowledge_faq.py` — debe seguir pasando tras mover query.
- [ ] `tests/unit/test_metrics_token.py` — métricas.
- [ ] Añadir test unitario: ningún fichero en `app/routes/` importa `app.models` excepto lista blanca (opcional, script grep en CI).

### Comprobaciones finales (punto 2)

```powershell
# Buscar imports prohibidos (debe tender a cero salvo excepciones documentadas)
rg "from app\.models" app/routes --glob "*.py"

uv run pytest tests/integration/test_knowledge_faq.py tests/unit/test_metrics_token.py -q
uv run mypy app/routes/web/knowledge.py app/routes/api/metrics.py
# Smoke manual: GET /knowledge, editar FAQ, GET /metrics/module1 con token
```

**Criterio de cierre:**

- [ ] `knowledge.py` no contiene `select(` ni import de `KnowledgeDocument` (salvo que quede excepción documentada temporal).
- [ ] Enums en rutas vienen de `app/schemas/`.
- [ ] `Agents.md` lista excepciones explícitas (metrics y/o SADM si aplica).

---

## Punto 3 — CI incompleto (solo evals, falta pipeline general)

> **Estado:** implementado (2026-06-30).

### Problema detectado

Solo existe `.github/workflows/evals.yml`. No hay workflow de **ruff + mypy + pytest** en PRs a `main`.

El repo sí tiene `.pre-commit-config.yaml` (ruff, mypy, hooks), pero depende de ejecución local.

### Por qué importa

Regresiones de tipos, estilo y tests no se detectan en PR → riesgo alto en un proyecto con capa LLM, webhooks y multi-tenant.

### Ficheros a crear / modificar

| Fichero | Acción |
|---------|--------|
| `.github/workflows/ci.yml` | **Crear** — pipeline principal |
| `.github/workflows/evals.yml` | Mantener (evals LLM con coste API) |
| `pyproject.toml` | Verificar que pytest/mypy/ruff están configurados (solo lectura salvo ajuste) |

### Cambios detallados — contenido de `ci.yml`

- [x] Trigger: `pull_request` y `push` a `main`.
- [x] Job `lint-and-typecheck`: `uv sync --frozen`, ruff check/format, mypy app.
- [x] Job `test` (tras lint): Postgres pgvector + Redis, `alembic upgrade head`, `alembic check`, pytest unit + integration.
- [x] Evals LLM **no** incluidos (permanecen en `evals.yml`).
- [x] Variables CI documentadas en `docs/environment-variables.md` § GitHub Actions.
- [x] Marker `real_llm` en pyproject + tests de API real excluidos en CI.

### Tests / infra

- [x] Unit tests: algunos requieren Postgres (job `test` levanta servicios).
- [x] Tests `real_llm` excluidos con `-m "integration and not real_llm"`.

### Comprobaciones finales (punto 3)

- [ ] Abrir PR → workflow `CI` verde en GitHub Actions (validar en remoto).
- [x] Simulación local lint: ruff + mypy verdes.
- [x] Marker `real_llm` registrado en `pyproject.toml`.

---

## Punto 4 — Seguridad fail-open en webhooks (WhatsApp y Telegram)

### Problema detectado

**Comportamiento actual (fail-open en dev):**

1. **WhatsApp** (`webhooks_whatsapp.py` L107-108): si `WHATSAPP_APP_SECRET` está vacío, **no se verifica** la firma HMAC. Cualquiera puede POSTear al endpoint.

2. **Telegram** (`webhooks_telegram.py` L91-102): si `integration.webhook_secret_enc` es `None`, **se omite** la verificación del header `X-Telegram-Bot-Api-Secret-Token`.

En ambos casos, tras procesar (o no), muchas ramas responden **HTTP 200** incluso con error de autenticación (ocultar error al proveedor — OK; pero el skip total en prod no lo es).

### Por qué importa

En **production**, un atacante que conozca la URL del webhook puede:

- Encolar mensajes falsos → coste LLM, spam RAG, abuso de **calendar tools** (crear/cancelar citas).
- Suplantar clientes externos sin validar origen Meta/Telegram.

### Ficheros afectados

| Fichero | Cambio |
|---------|--------|
| `app/config.py` | Flag explícito dev vs prod (opcional) |
| `app/routes/api/webhooks_whatsapp.py` | Fail-closed en prod |
| `app/routes/api/webhooks_telegram.py` | Fail-closed en prod |
| `app/services/channel_integration_service.py` | Validar secret obligatorio al crear integración (prod) |
| `tests/integration/test_whatsapp_webhook.py` | Casos prod sin secret |
| `tests/integration/test_telegram_webhook.py` | Casos prod sin secret |
| `docs/environment-variables.md` | Documentar variables y comportamiento |

### Cambios detallados

#### 4.1 — Regla de negocio (definir antes de codear)

| Entorno | WhatsApp sin `WHATSAPP_APP_SECRET` | Telegram sin `webhook_secret_enc` |
|---------|-------------------------------------|-----------------------------------|
| `development` | Permitir skip **solo** si flag explícito | Permitir skip **solo** si flag explícito |
| `staging` / `production` | **Rechazar** request (401/403) + log crítico | **Rechazar** request + log crítico |

**Flag propuesto (añadir a `config.py`):**

```python
webhook_allow_unsigned: bool = False  # True solo en dev local; env WEBHOOK_ALLOW_UNSIGNED
```

- [ ] Default `False`.
- [ ] En prod: nunca `True` (validar en startup si `app_env == "production"` y flag True → log error / raise).

#### 4.2 — WhatsApp POST

- [ ] Tras leer `app_secret`:
  - Si `not app_secret.strip()`:
    - Si `settings.is_dev and settings.webhook_allow_unsigned`: log warning y continuar (comportamiento actual).
    - Else: log `critical`, return `Response(status_code=503)` o `401` **sin** encolar job.
  - Si hay secret pero firma inválida: mantener 200 a Meta (no revelar) **pero no procesar** — ya ocurre; verificar que no encola.

- [ ] GET verify (`whatsapp_verify`): si `whatsapp_verify_token` vacío en prod → 403 (ya falla si `expected` vacío).

#### 4.3 — Telegram POST

- [ ] Tras cargar integración:
  - Si `not integration.webhook_secret_enc`:
    - Dev + `webhook_allow_unsigned` → continuar con warning.
    - Prod → log critical, return 200 **sin encolar** (Telegram reintenta; alternativa 403 si se prefiere fail-fast).
  - Si hay secret pero header inválido: mantener 200 sin encolar (comportamiento actual OK).

- [ ] Mover imports al tope (punto 1).

#### 4.4 — Alta de integraciones (preventivo)

- [ ] En flujo superadmin que crea integración Telegram: **generar y persistir** `webhook_secret_enc` siempre (ya debería ocurrir en Paso 21 — verificar).
- [ ] Documentar en UI admin: WhatsApp requiere `WHATSAPP_APP_SECRET` en Infisical prod.

### Tests a actualizar

- [ ] `test_whatsapp_webhook.py`:
  - Nuevo: `APP_ENV=production`, secret vacío, POST firmado/no → **no** debe encolar (`enqueue` not called).
  - Nuevo: `APP_ENV=development`, `WEBHOOK_ALLOW_UNSIGNED=true`, secret vacío → puede encolar (comportamiento dev).

- [ ] `test_telegram_webhook.py`:
  - Hoy existe test con `with_secret=False` que espera 200 sin encolar (sticker case). **Separar** caso “sin secret en prod” → no encolar + log.
  - Nuevo: prod + integración sin `webhook_secret_enc` → no encolar aunque haya texto válido.

### Comprobaciones finales (punto 4)

```powershell
uv run pytest tests/integration/test_whatsapp_webhook.py tests/integration/test_telegram_webhook.py -q

# Manual con APP_ENV=production (Infisical):
# - POST /api/webhooks/whatsapp sin X-Hub-Signature-256 → no debe encolar job ARQ
# - POST /api/webhooks/telegram/{id} sin header secret en integración sin secret → no encolar
```

**Criterio de cierre:**

- [ ] En `app_env=production`, imposible procesar mensaje sin verificación criptográfica configurada.
- [ ] Dev local sigue funcionando con `WEBHOOK_ALLOW_UNSIGNED=true` documentado en `Leeme.txt` / `docs/environment-variables.md`.
- [ ] Tests de integración cubren prod vs dev.

---

## Punto 5 — Facturas multi-IVA (limitación funcional)

### Problema detectado

El schema `Factura` solo tiene **un** `iva_percent` y **un** `iva_amount` escalares. Facturas con tipos mixtos (4 % / 10 % / 21 %) pierden desglose fiscal.

**Origen documentado en:** `Documentacion/PendienteImplementar.md` §3 bis, `Documentacion/Todo202607.md` A.2/B.1.

### Por qué importa

- **Total** puede cuadrar pero **no** sirve para cuaderno 303 / ERP / conciliación contable real.
- Prompt `extraction_v1.txt` pide el **% más alto** si hay varios → pérdida de información estructurada.
- `raw_extraction` JSONB guarda algo, pero no es queryable ni estable para informes.

### Decisión de producto (marcar antes de implementar)

- [ ] **Opción A — MVP:** documentar limitación; no cambiar schema (solo mejorar prompt para suma correcta de `iva_amount`).
- [ ] **Opción B — Contabilidad real (recomendado si cliente gestoría):** implementar desglose completo (este plan asume **Opción B**).

### Ficheros afectados (Opción B)

| Capa | Ficheros |
|------|----------|
| Schema LLM | `app/schemas/invoice.py`, `app/llm/prompts/extraction_v2.txt`, `app/llm/extraction.py` |
| BD | `app/models/invoice.py`, `migrations/versions/XXXX_invoice_vat_breakdown.py` |
| Servicio | `app/services/invoice_service.py` (`apply_extraction_result`) |
| UI | templates factura / document row / panel detalle |
| Evals | `app/evals/runners/extraction.py`, `app/evals/datasets/invoices_v1.json` (o `v2`) |
| Tests | `tests/unit/test_extraction.py`, `tests/unit/test_invoice_service.py` |

### Cambios detallados

#### 5.1 — Schema Pydantic (`app/schemas/invoice.py`)

- [ ] Añadir modelo:

```python
class DesgloseIVA(BaseModel):
    base: Decimal = Field(ge=0, description="Base imponible a este tipo")
    percent: Decimal = Field(ge=0, le=100, description="Tipo IVA (0, 4, 10, 21, ...)")
    amount: Decimal = Field(ge=0, description="Cuota IVA de este tramo")
```

- [ ] En `Factura`:
  - Añadir `desgloses_iva: list[DesgloseIVA] = Field(default_factory=list)`
  - Mantener `iva_percent` / `iva_amount` como **derivados** (compatibilidad):
    - `iva_amount` = sum(d.amount for d in desgloses) si lista no vacía
    - `iva_percent` = max(d.percent for d in desgloses) o el dominante por base
  - Actualizar `_check_totals_coherent`: `base_imponible + sum(amounts) ≈ total` (tolerancia existente)

#### 5.2 — Prompt

- [ ] Completar/usar `app/llm/prompts/extraction_v2.txt`: pedir **todos** los tramos de IVA con base, % y cuota; few-shot factura mixta hostelería.
- [ ] En `extraction.py`: `PROMPT_VERSION = "extraction_v2"`.
- [ ] Conservar `extraction_v1.txt` para comparar en evals (A/B).

#### 5.3 — Base de datos

**Opción mínima (JSONB):**

- [ ] Columna `vat_breakdown JSONB NULL` en `invoices` con array `[{base, percent, amount}]`.
- [ ] Seguir rellenando `iva_percent` / `iva_amount` agregados para dashboards legacy.

**Opción queryable (más trabajo):**

- [ ] Tabla `invoice_vat_breakdown` (invoice_id FK, tenant_id, base, percent, amount, position).

- [ ] Migración Alembic + RLS si tabla hija tiene `tenant_id`.

#### 5.4 — Servicio

- [ ] `apply_extraction_result`: persistir desglose desde `factura.desgloses_iva`.
- [ ] Si lista vacía pero campos escalares presentes (extracciones viejas): migrar a un solo tramo en lectura.

#### 5.5 — UI

- [ ] Mostrar tabla desglose en panel detalle factura (solo lectura al inicio; edición inline opcional fase 2).
- [ ] Export CSV futuro (Todo202607 B.2) debe incluir columnas o filas por tramo.

#### 5.6 — Evals

- [ ] Ampliar `_compare` en `extraction.py` para comparar listas de desgloses (tolerancia decimal).
- [ ] Añadir 3–5 PDFs/PNGs multi-IVA a dataset con ground truth explícito por tramo.
- [ ] Ejecutar runner y fijar baseline antes de merge.

### Tests a crear / actualizar

- [ ] Unit: validator `_check_totals_coherent` con 2 tramos IVA.
- [ ] Unit: `apply_extraction_result` persiste JSONB/tabla.
- [ ] Unit: extracción mock con `desgloses_iva` de 2 elementos.
- [ ] Evals: casos multi-IVA en dataset.

### Comprobaciones finales (punto 5)

```powershell
uv run alembic upgrade head
uv run pytest tests/unit/test_extraction.py tests/unit/test_invoice_service.py -q
infisical run -- uv run python -m app.evals.runners.extraction <tenant_uuid>

# Manual: subir factura mixta 10%+21%, verificar UI muestra dos filas de IVA y total cuadra
```

**Criterio de cierre:**

- [ ] Factura con dos tipos de IVA guarda desglose queryable.
- [ ] Evals multi-IVA ≥ umbral acordado (p. ej. accuracy campos críticos ≥ 95 % en tramos).
- [ ] `PendienteImplementar.md` §3 bis marcado como resuelto o referencia a este punto.

---

## Checklist global post-implementación

Cuando los 5 puntos estén hechos:

- [ ] `pre-commit run --all-files` verde
- [ ] CI GitHub Actions (`ci.yml` + `evals.yml`) verde en PR
- [ ] Actualizar `Documentacion/Todo202607.md` — marcar ítems cerrados
- [ ] Actualizar `Documentacion/PendienteImplementar.md` — corregir estado módulo 1.5 y multi-IVA
- [ ] Commit(s) convencionales separados por punto (recomendado):
  - `fix: resolve embedding model type and ruff issues`
  - `ci: add ruff mypy pytest workflow`
  - `fix: fail-closed webhook verification in production`
  - `refactor: routes use services instead of models imports`
  - `feat: invoice multi-vat breakdown extraction`

---

## Referencia — conversación y análisis origen

Hallazgos reportados por revisión externa (CDX), validados contra el código en junio 2026. Relacionado con el hilo de estudio sobre capa LLM, producción y gaps de empleo IA.

**Documentos relacionados:**

- `Documentacion/arquitectura.md` — §6 módulo 1, §8 LLM, §9 seguridad
- `Documentacion/Agents.md` — §3 capas, §9 tests
- `Documentacion/Todo202607.md` — backlog completo

---

*Fin CorreccionesCDX.md*
