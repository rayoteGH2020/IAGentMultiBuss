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
from app.models.appointment import Appointment
from app.models.audit_log import AuditLog
from app.models.base import Base, IdMixin, TimestampMixin
from app.models.business_hour import BusinessHour
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
from app.models.document_processing_attempt import (
    DocumentKind,
    DocumentProcessingAttempt,
    ProcessingAttemptStatus,
)
from app.models.invoice import Invoice, InvoiceLine, InvoiceStatus
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentKind,
    KnowledgeDocumentStatus,
)
from app.models.llm_call import LLMCall
from app.models.membership import Membership
from app.models.processing_charge import ProcessingCharge, ProcessingChargeStatus
from app.models.professional import Professional
from app.models.professional_specialty import ProfessionalSpecialty
from app.models.professional_working_hour import ProfessionalWorkingHour
from app.models.schedule_exception import ScheduleException
from app.models.scheduling_service import SchedulingService
from app.models.tenant import Tenant
from app.models.ticket import Ticket, TicketStatus
from app.models.usage_meter import UsageMeter
from app.models.user import User

__all__ = [
    "Appointment",
    "AuditLog",
    "Base",
    "BusinessHour",
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
    "DocumentKind",
    "DocumentProcessingAttempt",
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
    "ProcessingAttemptStatus",
    "ProcessingCharge",
    "ProcessingChargeStatus",
    "Professional",
    "ProfessionalSpecialty",
    "ProfessionalWorkingHour",
    "ScheduleException",
    "SchedulingService",
    "Tenant",
    "Ticket",
    "TicketStatus",
    "TimestampMixin",
    "UsageMeter",
    "User",
]
