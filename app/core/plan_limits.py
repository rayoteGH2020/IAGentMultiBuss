"""Helpers de limites cuantitativos por plan (Paso04)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.entitlements import Entitlements

MSG_DOCUMENTS_DAILY = "Has alcanzado el limite diario de documentos de tu plan."
MSG_DOCUMENT_RETRIES_DAILY = (
    "Has alcanzado el limite diario de reintentos de documentos de tu plan."
)
MSG_KNOWLEDGE_UPLOADS_DAILY = (
    "Has alcanzado el limite diario de subidas a la base de conocimiento de tu plan."
)
MSG_KNOWLEDGE_DOCS_MAX = "Has alcanzado el numero maximo de documentos de conocimiento de tu plan."
MSG_CHAT_MESSAGES_DAILY = "Has alcanzado el limite de mensajes de hoy."
MSG_CHAT_MESSAGES_USER_DAILY = "Has alcanzado tu limite personal de mensajes de chat de hoy."
MSG_CHANNEL_MESSAGES_HOURLY = "Has alcanzado el limite horario de mensajes del canal de tu plan."
MSG_VOICE_NOTES_HOURLY = "Has alcanzado el limite horario de notas de voz de tu plan."
MSG_MEMBERS_MAX = "Has alcanzado el numero maximo de miembros de tu plan."
MSG_LLM_BUDGET_MONTH = "Has alcanzado el presupuesto mensual de IA de tu plan."


def resolve_quota_cap(
    ents: Entitlements,
    code: str,
    *,
    platform_cap: int | None = None,
) -> int | None:
    """Techo efectivo: catalogo/override del plan, con cap opcional global (kill-switch).

    Returns:
        ``None`` = ilimitado (plan explicito null y sin cap de plataforma).
        ``0`` = bloqueado.
        Entero positivo = tope diario/horario/seats.
    """
    raw = ents.limit(code)
    if raw is None:
        if platform_cap is not None and platform_cap > 0:
            return platform_cap
        return None
    plan_cap = int(raw)
    if plan_cap <= 0:
        return 0
    if platform_cap is not None and platform_cap > 0:
        return min(plan_cap, platform_cap)
    return plan_cap


def resolve_budget_cap(
    ents: Entitlements,
    code: str,
    *,
    platform_cap_eur: Decimal | None = None,
) -> Decimal | None:
    """Presupuesto mensual EUR: ``None`` = ilimitado."""
    raw = ents.limit(code)
    if raw is None:
        if platform_cap_eur is not None and platform_cap_eur > 0:
            return platform_cap_eur
        return None
    if raw <= 0:
        return Decimal("0")
    if platform_cap_eur is not None and platform_cap_eur > 0:
        return min(raw, platform_cap_eur)
    return raw
