"""Registro de actividad para depurar el piloto (D029); separado de ``audit_log``."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLAlchemy resuelve Mapped[...] en runtime
from typing import Any
from uuid import UUID  # noqa: TC003

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Integer, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ActivityLog(Base):
    """Petición, job, evento de log o error. Sin datos personales salvo ``user_id``.

    Sin FK en ``tenant_id`` / ``user_id``: las filas se insertan en bloque desde un
    buffer y un tenant borrado entre medias tumbaría el lote. La purga por
    retención (``ACTIVITY_LOG_RETENTION_DAYS``) las elimina.
    """

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parent_request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status_code: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    htmx: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    attempt: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # none_as_null: sin datos extra se guarda NULL de SQL, no el JSON ``null``.
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
