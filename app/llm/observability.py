"""Política de trazas Langfuse: metadatos de evaluación, nunca contenido.

A Langfuse solo viaja lo necesario para evaluar la llamada (modelo, tokens,
coste, latencia, forma del resultado y modo de fallo). El contenido —documentos
subidos, mensajes de chat, consultas de búsqueda— se queda en Postgres/R2 y se
correlaciona con la traza mediante `llm_calls.langfuse_trace_id`.

`LANGFUSE_CAPTURE_CONTENT=true` permite capturar el contenido íntegro para
depurar prompts con datos sintéticos; `Settings` lo rechaza fuera de desarrollo.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from app.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

# Atributos donde los objetos multimodales de Instructor (PDF, Image) guardan
# el payload base64. Solo se usa su longitud, nunca el valor.
_MEDIA_PAYLOAD_ATTRS = ("data", "source", "base64", "b64_json", "content")


def capture_content_enabled() -> bool:
    """True si se permite enviar contenido íntegro (solo desarrollo)."""
    return get_settings().langfuse_capture_content


def messages_summary(messages: Sequence[Any]) -> dict[str, Any]:
    """Forma y volumen de una conversación, sin nada de su contenido.

    Args:
        messages: Lista de mensajes en formato OpenAI-compatible; el contenido
            puede ser texto, partes mixtas o media de Instructor (PDF/Image).

    Returns:
        Recuento de mensajes por rol, caracteres de texto y partes multimedia
        con su tamaño agregado en bytes de payload.
    """
    roles: Counter[str] = Counter()
    media: Counter[str] = Counter()
    text_chars = 0
    media_bytes = 0

    for message in messages:
        if isinstance(message, dict):
            roles[str(message.get("role", "unknown"))] += 1
            content: Any = message.get("content")
        else:
            roles["unknown"] += 1
            content = message
        chars, parts, size = _content_shape(content)
        text_chars += chars
        media.update(parts)
        media_bytes += size

    return {
        "messages": len(messages),
        "roles": dict(roles),
        "text_chars": text_chars,
        "media_parts": dict(media),
        "media_bytes": media_bytes,
    }


def result_summary(result: BaseModel | None) -> dict[str, Any]:
    """Forma del output estructurado: qué campos vinieron, no qué valor tienen.

    Permite detectar regresiones ("el modelo nuevo deja `total` vacío en el 8%
    de las extracciones") sin exponer un solo dato del documento.
    """
    if result is None:
        return {"result": None}

    data = result.model_dump(mode="json")
    if not isinstance(data, dict):
        return {"schema": type(result).__name__}

    summary: dict[str, Any] = {
        "schema": type(result).__name__,
        "fields_present": sorted(key for key, value in data.items() if not _is_empty(value)),
        "fields_missing": sorted(key for key, value in data.items() if _is_empty(value)),
        "list_sizes": {key: len(value) for key, value in data.items() if isinstance(value, list)},
    }
    confidence = data.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        summary["confidence"] = float(confidence)
    return summary


def text_summary(text: str | None) -> dict[str, Any]:
    """Longitud de un texto libre (respuesta de chat, consulta de búsqueda)."""
    return {"chars": len(text) if text else 0}


def trace_messages(messages: Sequence[Any]) -> Any:
    """Payload de entrada para `start_observation`."""
    if capture_content_enabled():
        return list(messages)
    return messages_summary(messages)


def trace_result(result: BaseModel | None) -> Any:
    """Payload de salida para un resultado estructurado."""
    if capture_content_enabled():
        return result.model_dump(mode="json") if result is not None else None
    return result_summary(result)


def trace_text(text: str | None) -> Any:
    """Payload para un texto libre."""
    if capture_content_enabled():
        return text
    return text_summary(text)


def trace_status_message(*, error_type: str | None, error: str | None) -> str | None:
    """Motivo de fallo para Langfuse: solo el tipo de excepción.

    El mensaje completo puede incluir la respuesta cruda del modelo cuando
    falla la validación del schema —y con ella el documento del cliente—, así
    que se queda en `llm_calls.error`, dentro de la BD con RLS.
    """
    if capture_content_enabled():
        return error
    return error_type


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _content_shape(content: Any) -> tuple[int, list[str], int]:
    """Devuelve (caracteres de texto, tipos de parte multimedia, bytes)."""
    if content is None:
        return 0, [], 0
    if isinstance(content, str):
        return len(content), [], 0
    if isinstance(content, (list, tuple)):
        chars = 0
        parts: list[str] = []
        size = 0
        for part in content:
            part_chars, part_types, part_size = _content_shape(part)
            chars += part_chars
            parts.extend(part_types)
            size += part_size
        return chars, parts, size
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return len(text), [], 0
        return 0, [str(content.get("type", "dict"))], _payload_size(content.get("data"))
    # Objeto multimodal de Instructor (PDF, Image) u otro tipo opaco.
    return 0, [type(content).__name__], _payload_size_from_attrs(content)


def _payload_size_from_attrs(part: Any) -> int:
    for attr in _MEDIA_PAYLOAD_ATTRS:
        size = _payload_size(getattr(part, attr, None))
        if size:
            return size
    return 0


def _payload_size(value: Any) -> int:
    if isinstance(value, (str, bytes)):
        return len(value)
    return 0
