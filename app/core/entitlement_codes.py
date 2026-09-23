"""Codigos canonicos de planes, features y limites (Paso02).

Fuente de verdad de nombres: Documentacion_V2/Planes_Entitlements.md.
La resolucion de capacidades vive en app.services.entitlement_service;
no usar `if tenant.plan == ...` en rutas ni workers.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

PLAN_CODE_BASIC: Final = "basic"
PLAN_CODE_MEDIUM: Final = "medium"
PLAN_CODE_HIGH: Final = "high"
PLAN_CODE_TOTAL: Final = "total"

PLAN_CODES: Final[frozenset[str]] = frozenset(
    {
        PLAN_CODE_BASIC,
        PLAN_CODE_MEDIUM,
        PLAN_CODE_HIGH,
        PLAN_CODE_TOTAL,
    }
)

# Alias legacy -> codigo canonico del catalogo.
LEGACY_PLAN_ALIASES: Final[dict[str, str]] = {
    "free": PLAN_CODE_BASIC,
}

FEATURE_DOCUMENTS: Final = "documents"
FEATURE_DOCUMENTS_CHAT: Final = "documents_chat"
FEATURE_KNOWLEDGE: Final = "knowledge"
FEATURE_KNOWLEDGE_CHAT: Final = "knowledge_chat"
FEATURE_CALENDAR_GOOGLE: Final = "calendar_google"
FEATURE_CALENDAR_VOICE: Final = "calendar_voice"
FEATURE_APPOINTMENTS: Final = "appointments"
FEATURE_CHANNEL_WHATSAPP: Final = "channel_whatsapp"
FEATURE_CHANNEL_TELEGRAM: Final = "channel_telegram"
# D011 (2026-09-23): modulo 3 Analytics SQL / BI sobre BD externa del cliente
# NO se implementara (decision de producto: fuera de alcance, no roadmap).
# Constante conservada solo para tests de kill-switch y docs historicas; no entra
# en FEATURE_CODES ni en ningun plan.
FEATURE_ANALYTICS: Final = "analytics"

FEATURE_CODES: Final[frozenset[str]] = frozenset(
    {
        FEATURE_DOCUMENTS,
        FEATURE_DOCUMENTS_CHAT,
        FEATURE_KNOWLEDGE,
        FEATURE_KNOWLEDGE_CHAT,
        FEATURE_CALENDAR_GOOGLE,
        FEATURE_CALENDAR_VOICE,
        FEATURE_APPOINTMENTS,
        FEATURE_CHANNEL_WHATSAPP,
        FEATURE_CHANNEL_TELEGRAM,
        # FEATURE_ANALYTICS omitido a proposito (D011 — no implementar modulo 3).
    }
)

LIMIT_DOCUMENTS_PER_DAY: Final = "documents_per_day"
LIMIT_DOCUMENT_RETRIES_PER_DAY: Final = "document_retries_per_day"
LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: Final = "knowledge_uploads_per_day"
LIMIT_KNOWLEDGE_DOCS_MAX: Final = "knowledge_docs_max"
LIMIT_CHAT_MESSAGES_PER_DAY: Final = "chat_messages_per_day"
LIMIT_CHANNEL_MESSAGES_PER_HOUR: Final = "channel_messages_per_hour"
LIMIT_VOICE_NOTES_PER_HOUR: Final = "voice_notes_per_hour"
LIMIT_MEMBERS_MAX: Final = "members_max"
LIMIT_LLM_BUDGET_EUR_MONTH: Final = "llm_budget_eur_month"
LIMIT_CHANNEL_EXTERNAL_SLOTS: Final = "channel_external_slots"

LIMIT_CODES: Final[frozenset[str]] = frozenset(
    {
        LIMIT_DOCUMENTS_PER_DAY,
        LIMIT_DOCUMENT_RETRIES_PER_DAY,
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY,
        LIMIT_KNOWLEDGE_DOCS_MAX,
        LIMIT_CHAT_MESSAGES_PER_DAY,
        LIMIT_CHANNEL_MESSAGES_PER_HOUR,
        LIMIT_VOICE_NOTES_PER_HOUR,
        LIMIT_MEMBERS_MAX,
        LIMIT_LLM_BUDGET_EUR_MONTH,
        LIMIT_CHANNEL_EXTERNAL_SLOTS,
    }
)

ENTITLEMENT_KIND_FEATURE: Final = "feature"
ENTITLEMENT_KIND_LIMIT: Final = "limit"

OVERRIDE_SETTINGS_KEY: Final = "entitlements_override"

FEATURE_UI_LABELS: Final[dict[str, str]] = {
    FEATURE_DOCUMENTS: "Documentos",
    FEATURE_DOCUMENTS_CHAT: "Chat documental",
    FEATURE_KNOWLEDGE: "Base de conocimiento",
    FEATURE_KNOWLEDGE_CHAT: "Chat sobre conocimiento",
    FEATURE_CALENDAR_GOOGLE: "Calendario Google",
    FEATURE_CALENDAR_VOICE: "Calendario por voz",
    FEATURE_APPOINTMENTS: "Citas",
    FEATURE_CHANNEL_WHATSAPP: "WhatsApp",
    FEATURE_CHANNEL_TELEGRAM: "Telegram",
    # FEATURE_ANALYTICS: "Analytics",  # D011 — no implementar; sin UI ni gates.
}


def feature_ui_label(code: str) -> str:
    return FEATURE_UI_LABELS.get(code, code)


# Matriz inicial (Planes_Entitlements.md §4-§5). Solo features habilitadas.
PLAN_FEATURES: Final[dict[str, frozenset[str]]] = {
    PLAN_CODE_BASIC: frozenset({FEATURE_DOCUMENTS, FEATURE_DOCUMENTS_CHAT}),
    PLAN_CODE_MEDIUM: frozenset(
        {
            FEATURE_DOCUMENTS,
            FEATURE_DOCUMENTS_CHAT,
            FEATURE_KNOWLEDGE,
            FEATURE_KNOWLEDGE_CHAT,
        }
    ),
    PLAN_CODE_HIGH: frozenset(
        {
            FEATURE_DOCUMENTS,
            FEATURE_DOCUMENTS_CHAT,
            FEATURE_KNOWLEDGE,
            FEATURE_KNOWLEDGE_CHAT,
            FEATURE_APPOINTMENTS,
            FEATURE_CHANNEL_WHATSAPP,
            FEATURE_CHANNEL_TELEGRAM,
        }
    ),
    # total = todas las features activas (FEATURE_CODES ya excluye analytics / D011).
    PLAN_CODE_TOTAL: frozenset(FEATURE_CODES),
}

PLAN_LIMITS: Final[dict[str, dict[str, Decimal | None]]] = {
    PLAN_CODE_BASIC: {
        LIMIT_DOCUMENTS_PER_DAY: Decimal("30"),
        LIMIT_DOCUMENT_RETRIES_PER_DAY: Decimal("10"),
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: Decimal("0"),
        LIMIT_KNOWLEDGE_DOCS_MAX: Decimal("0"),
        LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("40"),
        LIMIT_CHANNEL_MESSAGES_PER_HOUR: Decimal("0"),
        LIMIT_VOICE_NOTES_PER_HOUR: Decimal("0"),
        LIMIT_MEMBERS_MAX: Decimal("3"),
        LIMIT_LLM_BUDGET_EUR_MONTH: Decimal("5"),
        LIMIT_CHANNEL_EXTERNAL_SLOTS: Decimal("0"),
    },
    PLAN_CODE_MEDIUM: {
        LIMIT_DOCUMENTS_PER_DAY: Decimal("100"),
        LIMIT_DOCUMENT_RETRIES_PER_DAY: Decimal("30"),
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: Decimal("20"),
        LIMIT_KNOWLEDGE_DOCS_MAX: Decimal("50"),
        LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("80"),
        LIMIT_CHANNEL_MESSAGES_PER_HOUR: Decimal("0"),
        LIMIT_VOICE_NOTES_PER_HOUR: Decimal("0"),
        LIMIT_MEMBERS_MAX: Decimal("10"),
        LIMIT_LLM_BUDGET_EUR_MONTH: Decimal("25"),
        LIMIT_CHANNEL_EXTERNAL_SLOTS: Decimal("0"),
    },
    PLAN_CODE_HIGH: {
        LIMIT_DOCUMENTS_PER_DAY: Decimal("300"),
        LIMIT_DOCUMENT_RETRIES_PER_DAY: Decimal("100"),
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: Decimal("50"),
        LIMIT_KNOWLEDGE_DOCS_MAX: Decimal("200"),
        LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("150"),
        LIMIT_CHANNEL_MESSAGES_PER_HOUR: Decimal("60"),
        LIMIT_VOICE_NOTES_PER_HOUR: Decimal("0"),
        LIMIT_MEMBERS_MAX: Decimal("25"),
        LIMIT_LLM_BUDGET_EUR_MONTH: Decimal("80"),
        LIMIT_CHANNEL_EXTERNAL_SLOTS: Decimal("2"),
    },
    PLAN_CODE_TOTAL: {
        LIMIT_DOCUMENTS_PER_DAY: Decimal("1000"),
        LIMIT_DOCUMENT_RETRIES_PER_DAY: Decimal("500"),
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: Decimal("200"),
        LIMIT_KNOWLEDGE_DOCS_MAX: Decimal("1000"),
        LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("400"),
        LIMIT_CHANNEL_MESSAGES_PER_HOUR: Decimal("120"),
        LIMIT_VOICE_NOTES_PER_HOUR: Decimal("60"),
        LIMIT_MEMBERS_MAX: Decimal("100"),
        # null declarado = unlimited (piloto puede overridear a soft-cap).
        LIMIT_LLM_BUDGET_EUR_MONTH: None,
        LIMIT_CHANNEL_EXTERNAL_SLOTS: Decimal("2"),
    },
}

PLAN_META: Final[dict[str, tuple[str, str, int]]] = {
    # code -> (name, description, sort_order)
    PLAN_CODE_BASIC: (
        "Basico",
        "Documentos ligeros y chat documental basico.",
        10,
    ),
    PLAN_CODE_MEDIUM: (
        "Medio",
        "Basico + knowledge/RAG y chat sobre knowledge.",
        20,
    ),
    PLAN_CODE_HIGH: (
        "Alto",
        "Medio + citas y canales externos.",
        30,
    ),
    PLAN_CODE_TOTAL: (
        "Total",
        # D011: sin Analytics/BI; total = resto del producto + limites altos.
        "Todo el producto activo: calendario Google, canales, citas y limites altos.",
        40,
    ),
}


def normalize_plan_code(raw: str | None) -> str:
    """Normaliza alias legacy; conserva codigos desconocidos para fail-closed.

    - ``None`` / vacio -> ``basic`` (default de alta).
    - ``free`` -> ``basic``.
    - Cualquier otro string se deja (lower); si no existe en catalogo,
      ``resolve_entitlements`` responde fail-closed.
    """
    if raw is None:
        return PLAN_CODE_BASIC
    code = raw.strip().lower()
    if not code:
        return PLAN_CODE_BASIC
    if code in LEGACY_PLAN_ALIASES:
        return LEGACY_PLAN_ALIASES[code]
    return code


def is_known_feature(code: str) -> bool:
    return code in FEATURE_CODES


def is_known_limit(code: str) -> bool:
    return code in LIMIT_CODES
