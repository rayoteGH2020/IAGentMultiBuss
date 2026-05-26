from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LLMCall(Base):
    """Registro inmutable de cada llamada a un modelo LLM.

    Es la fuente de verdad para observabilidad de costes, latencias y errores
    por tenant. Cada registro se crea en llm/client.py y nunca se actualiza;
    solo se inserta (append-only). Por eso no tiene updated_at.
    """

    __tablename__ = "llm_calls"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # task: tipo de operación LLM ("extraction", "chat", "sql", "classify",
    # "embedding"). String en lugar de Enum para no tener que migrar el tipo
    # cada vez que se añade un nuevo módulo o tarea al sistema.
    task: Mapped[str] = mapped_column(String(50), nullable=False)
    # model: identificador exacto del modelo usado, p. ej. "gemini-2.5-flash"
    # o "claude-sonnet-4-6". String(100) porque los IDs de modelo con versión
    # pueden ser largos.
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    # provider: "anthropic" | "google" | "voyage". Permite agrupar costes por
    # proveedor en el dashboard de métricas sin parsear el campo model.
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    # Nullable: las llamadas de embedding no usan prompts versionados porque
    # no tienen un system prompt propio; solo los modelos de completado los usan.
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Nombre original del fichero subido cuando la llamada procesa un documento.
    source_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # server_default="0" en lugar de default Python: si la inserción ocurre
    # antes de tener los tokens (p. ej. el registro se crea al inicio de la
    # llamada y se actualiza al final), Postgres garantiza el valor por defecto.
    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    # Numeric(10, 6): 6 decimales para coste en EUR porque las llamadas
    # individuales son muy baratas (del orden de 0,000050 EUR por llamada).
    # 6 decimales evitan perder precisión al acumular costes para el billing.
    cost_eur: Mapped[Decimal] = mapped_column(Numeric(10, 6), server_default="0", nullable=False)
    # Latencia en milisegundos enteros: coherente con las métricas del eval
    # runner y los percentiles del endpoint /metrics/module1.
    latency_ms: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)

    # "ok" | "error". String (no Enum) por la misma razón que task: extensible
    # sin migración. server_default="ok" asume éxito; se cambia a "error" si
    # la llamada falla antes de completarse.
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ok")
    # Text (sin límite): los mensajes de error del LLM o las trazas de Python
    # pueden ser muy largos. A diferencia de Invoice.error_message, aquí no
    # se trunca porque el diagnóstico de fallos LLM necesita el error completo.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Vincula este registro con la traza de Langfuse para inspección profunda
    # de prompts, tokens y timing en el panel de Langfuse self-hosted.
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # server_default=func.now(): timestamp asignado por Postgres al insertar.
    # No hay updated_at porque los registros son inmutables (append-only).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Índice compuesto tenant+created_at: cubre la query del endpoint de
        # métricas (WHERE tenant_id ... AND created_at >= since) y las queries
        # de billing por período (GROUP BY tenant_id, date_trunc('month', created_at)).
        Index("ix_llm_calls_tenant_created", "tenant_id", "created_at"),
    )
