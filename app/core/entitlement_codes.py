"""Codigos canonicos de planes, features y limites (Paso02).

Fuente de verdad de nombres: Documentacion_V2/Planes_Entitlements.md.
La resolucion de capacidades vive en app.services.entitlement_service;
no usar `if tenant.plan == ...` en rutas ni workers.

Catalogo comercial (D012, 2026-09-23): ``basic`` | ``advanced`` | ``premium``.
Limites duros en los tres: al superar Avanzado → upgrade a Premium; al superar
Premium → solo override SADM / contrato custom (no self-serve).

Calendar Google y voz-calendario siguen en FEATURE_CODES (codigo existente) pero
NO entran en ningun plan publicado (D012 — no evolucionar ni publicitar).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

PLAN_CODE_BASIC: Final = "basic"
PLAN_CODE_ADVANCED: Final = "advanced"
PLAN_CODE_PREMIUM: Final = "premium"

PLAN_CODES: Final[frozenset[str]] = frozenset(
    {
        PLAN_CODE_BASIC,
        PLAN_CODE_ADVANCED,
        PLAN_CODE_PREMIUM,
    }
)

# Alias legacy / UI → codigo canonico del catalogo.
LEGACY_PLAN_ALIASES: Final[dict[str, str]] = {
    "free": PLAN_CODE_BASIC,
    "basico": PLAN_CODE_BASIC,
    "medium": PLAN_CODE_BASIC,
    "high": PLAN_CODE_ADVANCED,
    "avanzado": PLAN_CODE_ADVANCED,
    "total": PLAN_CODE_PREMIUM,
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
# D011: Analytics SQL / BI — no implementar; constante solo para kill-switch/docs.
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
        # FEATURE_ANALYTICS omitido (D011).
    }
)

# Features vendibles en planes publicos (sin calendar_* — D012).
_BASE_PRODUCT: Final[frozenset[str]] = frozenset(
    {
        FEATURE_DOCUMENTS,
        FEATURE_DOCUMENTS_CHAT,
        FEATURE_KNOWLEDGE,
        FEATURE_KNOWLEDGE_CHAT,
    }
)
_ADVANCED_PRODUCT: Final[frozenset[str]] = _BASE_PRODUCT | frozenset(
    {
        FEATURE_APPOINTMENTS,
        FEATURE_CHANNEL_WHATSAPP,
        FEATURE_CHANNEL_TELEGRAM,
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
}


def feature_ui_label(code: str) -> str:
    return FEATURE_UI_LABELS.get(code, code)


# Matriz comercial (Planes_Entitlements.md). calendar_* ausente a proposito (D012).
PLAN_FEATURES: Final[dict[str, frozenset[str]]] = {
    PLAN_CODE_BASIC: _BASE_PRODUCT,
    PLAN_CODE_ADVANCED: _ADVANCED_PRODUCT,
    PLAN_CODE_PREMIUM: _ADVANCED_PRODUCT,
}

# Limites duros (no null). Superar Avanzado → Premium; superar Premium → SADM/custom.
PLAN_LIMITS: Final[dict[str, dict[str, Decimal | None]]] = {
    PLAN_CODE_BASIC: {
        LIMIT_DOCUMENTS_PER_DAY: Decimal("50"),
        LIMIT_DOCUMENT_RETRIES_PER_DAY: Decimal("20"),
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: Decimal("25"),
        LIMIT_KNOWLEDGE_DOCS_MAX: Decimal("100"),
        LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("100"),
        LIMIT_CHANNEL_MESSAGES_PER_HOUR: Decimal("0"),
        LIMIT_VOICE_NOTES_PER_HOUR: Decimal("0"),
        LIMIT_MEMBERS_MAX: Decimal("5"),
        LIMIT_LLM_BUDGET_EUR_MONTH: Decimal("30"),
        LIMIT_CHANNEL_EXTERNAL_SLOTS: Decimal("0"),
    },
    PLAN_CODE_ADVANCED: {
        LIMIT_DOCUMENTS_PER_DAY: Decimal("200"),
        LIMIT_DOCUMENT_RETRIES_PER_DAY: Decimal("80"),
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: Decimal("60"),
        LIMIT_KNOWLEDGE_DOCS_MAX: Decimal("400"),
        LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("250"),
        LIMIT_CHANNEL_MESSAGES_PER_HOUR: Decimal("80"),
        LIMIT_VOICE_NOTES_PER_HOUR: Decimal("0"),
        LIMIT_MEMBERS_MAX: Decimal("15"),
        LIMIT_LLM_BUDGET_EUR_MONTH: Decimal("100"),
        LIMIT_CHANNEL_EXTERNAL_SLOTS: Decimal("2"),
    },
    PLAN_CODE_PREMIUM: {
        LIMIT_DOCUMENTS_PER_DAY: Decimal("800"),
        LIMIT_DOCUMENT_RETRIES_PER_DAY: Decimal("300"),
        LIMIT_KNOWLEDGE_UPLOADS_PER_DAY: Decimal("200"),
        LIMIT_KNOWLEDGE_DOCS_MAX: Decimal("1500"),
        LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("600"),
        LIMIT_CHANNEL_MESSAGES_PER_HOUR: Decimal("200"),
        LIMIT_VOICE_NOTES_PER_HOUR: Decimal("0"),
        LIMIT_MEMBERS_MAX: Decimal("40"),
        LIMIT_LLM_BUDGET_EUR_MONTH: Decimal("250"),
        LIMIT_CHANNEL_EXTERNAL_SLOTS: Decimal("2"),
    },
}

PLAN_META: Final[dict[str, tuple[str, str, int]]] = {
    PLAN_CODE_BASIC: (
        "Basico",
        "Documentos, chat documental, knowledge/RAG y chat sobre knowledge.",
        10,
    ),
    PLAN_CODE_ADVANCED: (
        "Avanzado",
        "Basico + citas internas (BBDD saas) + WhatsApp/Telegram (chat knowledge).",
        20,
    ),
    PLAN_CODE_PREMIUM: (
        "Premium",
        "Mismas capacidades que Avanzado con limites superiores (duros).",
        30,
    ),
}


def normalize_plan_code(raw: str | None) -> str:
    """Normaliza alias legacy; conserva codigos desconocidos para fail-closed.

    - ``None`` / vacio -> ``basic`` (default de alta).
    - ``free`` / ``medium`` -> ``basic``; ``high`` -> ``advanced``; ``total`` -> ``premium``.
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
