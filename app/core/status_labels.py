"""Etiquetas en español de los estados internos (los valores se guardan en inglés).

Un único mapa para toda la UI: documentos, conocimiento, citas y llamadas LLM.
Un valor que no esté aquí se muestra tal cual (fail soft) para no romper la
plantilla; al añadir un estado nuevo a un enum visible, añadir su etiqueta.
"""

from enum import Enum

STATUS_LABELS: dict[str, str] = {
    # Procesado de documentos (InvoiceStatus, TicketStatus, ContractStatus, ...)
    "pending": "Pendiente",
    "processing": "Procesando",
    "ready": "Listo",
    "failed": "Error",
    "reviewed": "Revisado",
    "quota_pending": "Pendiente de cupo",
    # Conocimiento (KnowledgeDocumentStatus)
    "indexing": "Indexando",
    # Citas (AppointmentStatus)
    "scheduled": "Programada",
    "confirmed": "Confirmada",
    "cancelled": "Cancelada",
    "completed": "Completada",
    "no_show": "No se presentó",
    # Llamadas LLM (llm_calls.status)
    "ok": "Correcta",
    "error": "Error",
}


def status_label(value: object) -> str:
    """Etiqueta en español de un estado (acepta el enum o su valor)."""
    if value is None:
        return ""
    key = value.value if isinstance(value, Enum) else str(value)
    return STATUS_LABELS.get(key, key)
