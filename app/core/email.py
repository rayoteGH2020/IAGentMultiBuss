from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib
import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)


def smtp_tls_flags(settings: Settings) -> tuple[bool, bool]:
    """Devuelve ``(use_tls, start_tls)`` mutuamente excluyentes.

    - Puerto 465 / ``SMTP_SSL=true``: TLS desde el handshake (``use_tls``).
    - Puerto 587 / ``SMTP_STARTTLS=true``: upgrade tras conectar (``start_tls``).
    Si ambos flags vienen a true, gana SSL implícito (465).
    """
    use_tls = bool(settings.smtp_ssl)
    start_tls = bool(settings.smtp_starttls) and not use_tls
    return use_tls, start_tls


async def send_email(*, to: str, subject: str, body: str) -> None:
    """Send a plain-text email via SMTP.

    Skips silently if smtp_host is not configured (development without email).
    """
    settings = get_settings()
    if not settings.smtp_host:
        logger.debug("email.skipped", reason="smtp_host not configured", to=to, subject=subject)
        return

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    use_tls, start_tls = smtp_tls_flags(settings)
    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password.get_secret_value() or None,
        use_tls=use_tls,
        start_tls=start_tls,
    )
    logger.info(
        "email.sent",
        to=to,
        subject=subject,
        port=settings.smtp_port,
        use_tls=use_tls,
        start_tls=start_tls,
    )
