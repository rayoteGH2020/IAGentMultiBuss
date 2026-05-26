"""Cifrado simétrico para tokens OAuth y credenciales sensibles (Paso 17)."""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken

from app.core.errors import ValidationError


def _fernet(key: str) -> Fernet:
    if not key.strip():
        raise ValidationError("ENCRYPTION_KEY is not configured")
    raw = key.strip()
    if len(raw) == 44:
        try:
            return Fernet(raw.encode())
        except (ValueError, TypeError) as exc:
            raise ValidationError("ENCRYPTION_KEY is not a valid Fernet key") from exc
    derived = base64.urlsafe_b64encode(raw.encode()[:32].ljust(32, b"\0"))
    try:
        return Fernet(derived)
    except (ValueError, TypeError) as exc:
        raise ValidationError("ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_token(plain: str, key: str) -> bytes:
    """Cifra texto plano con Fernet (AES-128-CBC + HMAC)."""
    if not plain:
        raise ValidationError("Cannot encrypt empty token")
    return bytes(_fernet(key).encrypt(plain.encode()))


def decrypt_token(cipher: bytes, key: str) -> str:
    """Descifra bytes Fernet a texto plano."""
    try:
        return bytes(_fernet(key).decrypt(cipher)).decode()
    except InvalidToken as exc:
        raise ValidationError("Failed to decrypt token") from exc
