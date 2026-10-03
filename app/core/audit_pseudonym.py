"""Seudónimos para la metadata de ``audit_log`` (Backlog P2c-7).

La metadata de auditoría no guarda datos personales en claro (emails, nombres de
fichero, nombres de profesionales): guarda un HMAC-SHA256 con
``AUDIT_PSEUDONYM_KEY``. Así una fuga no los revela, pero sigue siendo posible
responder «qué se hizo con ana@empresa.com» recalculando el seudónimo con el dato
que se conoce (``scripts/audit_lookup.py``), incluso después de anonimizar al
usuario (D021).

La clave es distinta de ``APP_SECRET_KEY`` y no se rota: si cambiara, los
seudónimos antiguos ya no se podrían recalcular.
"""

from __future__ import annotations

import hashlib
import hmac
import unicodedata
from pathlib import PurePath
from typing import Literal

from app.config import get_settings

RefKind = Literal["email", "filename", "name"]

# 128 bits: sin colisiones prácticas entre los valores de un tenant.
_REF_HEX_CHARS = 32
_FALLBACK_CONTEXT = b"audit-pseudonym-fallback-v1"


def _key() -> bytes:
    settings = get_settings()
    if settings.audit_pseudonym_key is not None:
        return settings.audit_pseudonym_key.get_secret_value().encode("utf-8")
    # Fuera de producción (el validador la exige allí): derivada de APP_SECRET_KEY.
    secret = settings.app_secret_key.get_secret_value().encode("utf-8")
    return hmac.new(secret, _FALLBACK_CONTEXT, hashlib.sha256).digest()


def normalize(value: str, kind: RefKind) -> str:
    """Forma canónica antes del HMAC: el mismo dato da el mismo seudónimo.

    Emails y nombres de fichero sin distinguir mayúsculas; nombres de persona,
    además, sin tildes y con los espacios colapsados («Ana  García» = «ana garcia»).
    """
    text = value.strip().casefold()
    if kind == "name":
        decomposed = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        text = " ".join(text.split())
    return text


def audit_ref(value: object, kind: RefKind) -> str | None:
    """Seudónimo estable del dato; ``None`` si está vacío."""
    if value is None:
        return None
    text = normalize(str(value), kind)
    if not text:
        return None
    message = kind.encode("utf-8") + b":" + text.encode("utf-8")
    return hmac.new(_key(), message, hashlib.sha256).hexdigest()[:_REF_HEX_CHARS]


def file_extension(filename: str | None) -> str | None:
    """Extensión del fichero en minúsculas (``.pdf``); no identifica a nadie."""
    if not filename:
        return None
    suffix = PurePath(filename).suffix.lower()
    return suffix[:10] or None


def file_metadata(filename: str | None, *, sha256: str | None = None) -> dict[str, str]:
    """Campos de fichero para la auditoría: extensión, seudónimo del nombre y hash."""
    meta: dict[str, str] = {}
    if (ext := file_extension(filename)) is not None:
        meta["file_ext"] = ext
    if (ref := audit_ref(filename, "filename")) is not None:
        meta["filename_ref"] = ref
    if sha256:
        meta["file_sha256"] = sha256
    return meta
