"""Transcripción de audio y extracción de borrador de evento de voz (Paso 23).

Análogo a extraction.py para facturas. Toda llamada pasa por LLMClient;
no se invoca google-genai directamente desde aquí.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.llm.client import get_llm_client
from app.llm.prompts_loader import render_prompt
from app.schemas.calendar import _VoiceEventExtraction

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

TRANSCRIBE_PROMPT_VERSION = "voice_transcribe_v1"
DRAFT_PROMPT_VERSION = "voice_event_v1"


async def transcribe_audio(
    audio: bytes,
    mime_type: str,
    *,
    tenant_id: UUID,
    db: AsyncSession,
) -> str:
    """Transcribe el audio a texto literal usando Gemini audio nativo.

    Args:
        audio: Bytes del fichero de audio ya validados (MIME + tamaño).
        mime_type: MIME normalizado (p. ej. "audio/ogg").
        tenant_id: Tenant activo para audit y RLS.
        db: Sesión async activa.

    Returns:
        Transcripción literal del audio como string.
    """
    from app.llm.prompts_loader import load_prompt

    system_prompt = load_prompt(TRANSCRIBE_PROMPT_VERSION)
    client = get_llm_client()
    transcript = await client.transcribe(
        audio=audio,
        mime_type=mime_type,
        tenant_id=tenant_id,
        db=db,
        system_prompt=system_prompt,
        prompt_version=TRANSCRIBE_PROMPT_VERSION,
    )
    logger.info(
        "voice.transcribed",
        tenant_id=str(tenant_id),
        mime_type=mime_type,
        audio_bytes=len(audio),
        transcript_chars=len(transcript),
    )
    return transcript


async def draft_event_from_transcript(
    transcript: str,
    *,
    now_iso: str,
    timezone: str,
    default_duration_min: int,
    tenant_id: UUID,
    db: AsyncSession,
) -> _VoiceEventExtraction:
    """Extrae la estructura del evento a partir de la transcripción.

    Usa Instructor con _VoiceEventExtraction como response_model para obtener
    campos tipados (summary, start, end, confidence, needs_clarification).
    El servicio asignará el campo `transcript` en VoiceEventDraft.

    Args:
        transcript: Texto literal devuelto por transcribe_audio().
        now_iso: Fecha/hora actual en ISO 8601 con offset (para resolver
            referencias relativas como "mañana" o "el viernes").
        timezone: Zona horaria IANA (p. ej. "Europe/Madrid").
        default_duration_min: Duración por defecto si el usuario no indica fin.
        tenant_id: Tenant activo para audit y RLS.
        db: Sesión async activa.

    Returns:
        _VoiceEventExtraction con los campos del evento extraídos.
    """
    prompt = render_prompt(
        DRAFT_PROMPT_VERSION,
        transcript=transcript,
        now_iso=now_iso,
        timezone=timezone,
        default_duration_min=default_duration_min,
    )
    messages = [{"role": "user", "content": prompt}]
    client = get_llm_client()
    result = await client.complete(
        task="classify",
        messages=messages,
        response_model=_VoiceEventExtraction,
        tenant_id=tenant_id,
        db=db,
        prompt_version=DRAFT_PROMPT_VERSION,
    )
    logger.info(
        "voice.draft_extracted",
        tenant_id=str(tenant_id),
        confidence=result.result.confidence,
        needs_clarification=result.result.needs_clarification,
    )
    return result.result
