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
from app.models.base import Base, IdMixin, TimestampMixin
from app.models.invoice import Invoice, InvoiceLine, InvoiceStatus
from app.models.llm_call import LLMCall
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User

__all__ = [
    "Base",
    "IdMixin",
    "Invoice",
    "InvoiceLine",
    "InvoiceStatus",
    "LLMCall",
    "Membership",
    "Tenant",
    "TimestampMixin",
    "User",
]
