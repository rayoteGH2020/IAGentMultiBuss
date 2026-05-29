from email.message import EmailMessage

import aiosmtplib
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


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

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password.get_secret_value(),
        start_tls=settings.smtp_starttls,
    )
    logger.info("email.sent", to=to, subject=subject)
