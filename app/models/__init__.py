# Este __init__ tiene dos propósitos críticos:
#
# 1. API pública: permite `from app.models import Invoice` en lugar de
#    `from app.models.invoice import Invoice`, desacoplando a los importadores
#    de la organización interna del paquete.
#
# 2. Registro de modelos en Alembic: para que `alembic revision --autogenerate`
#    detecte los modelos y genere migraciones correctas, TODOS los modelos deben
#    estar importados (y por tanto registrados en Base.metadata) antes de que
#    Alembic inspeccione el metadata. Importarlos aquí garantiza que basta con
#    `from app.models import Base` en env.py de Alembic para tenerlos todos.
from app.models.audit_log import AuditLog
from app.models.base import Base, IdMixin, TimestampMixin
from app.models.calendar_integration import (
    CalendarIntegration,
    CalendarIntegrationProvider,
    CalendarIntegrationStatus,
)
from app.models.channel_integration import (
    ChannelIntegration,
    ChannelIntegrationStatus,
    ChannelType,
)
from app.models.channel_response_cache import ChannelResponseCache
from app.models.chat import ChatMessage, ChatMessageRole, ChatThread
from app.models.conversation import ChannelMessage, Conversation
from app.models.doc_type import DocType, DocTypeCode
from app.models.invoice import Invoice, InvoiceLine, InvoiceStatus
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentKind,
    KnowledgeDocumentStatus,
)
from app.models.llm_call import LLMCall
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.ticket import Ticket, TicketStatus
from app.models.usage_meter import UsageMeter
from app.models.user import User

__all__ = [
    "AuditLog",
    "Base",
    "CalendarIntegration",
    "CalendarIntegrationProvider",
    "CalendarIntegrationStatus",
    "ChannelIntegration",
    "ChannelIntegrationStatus",
    "ChannelMessage",
    "ChannelResponseCache",
    "ChannelType",
    "ChatMessage",
    "ChatMessageRole",
    "ChatThread",
    "Conversation",
    "DocType",
    "DocTypeCode",
    "IdMixin",
    "Invoice",
    "InvoiceLine",
    "InvoiceStatus",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeDocumentKind",
    "KnowledgeDocumentStatus",
    "LLMCall",
    "Membership",
    "Tenant",
    "Ticket",
    "TicketStatus",
    "TimestampMixin",
    "UsageMeter",
    "User",
]
