"""Configuración del worker ARQ.

ARQ lee esta clase al arrancar el proceso worker:
    uv run arq app.jobs.settings.WorkerSettings
Todos los atributos son leídos como atributos de clase, no de instancia.
"""

from typing import Any, ClassVar

from arq import cron
from arq import func as arq_func
from arq.connections import RedisSettings

from app.config import get_settings
from app.core.logging import configure_worker_logging
from app.jobs.budget_alert_jobs import send_llm_budget_alert
from app.jobs.channel_jobs import process_channel_message
from app.jobs.contract_jobs import process_contract
from app.jobs.insurance_jobs import process_insurance
from app.jobs.invoice_jobs import process_invoice
from app.jobs.knowledge_jobs import index_knowledge_document
from app.jobs.membership_jobs import expire_member_removals
from app.jobs.plan_jobs import apply_scheduled_plan_changes
from app.jobs.provider_alert_jobs import send_llm_provider_billing_alert
from app.jobs.ticket_jobs import process_ticket


async def startup(_ctx: dict[str, Any]) -> None:
    """Configura el logging del worker tras el ``dictConfig`` del CLI de arq."""
    configure_worker_logging()


class WorkerSettings:
    # ARQ conecta al mismo Redis que la app para leer la cola de jobs.
    # Se parsea una sola vez al arrancar el worker (atributo de clase).
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)

    # Registro de funciones que el worker puede ejecutar. ARQ enruta cada job
    # por el nombre de la función; si una función no aparece aquí, los jobs
    # encolados con ese nombre se ignorarán silenciosamente.
    # index_knowledge_document usa arq_func() para sobreescribir el timeout
    # global (180 s) con 600 s: los embeddings de documentos largos son lentos.
    functions: ClassVar[list[object]] = [
        process_invoice,
        process_ticket,
        process_contract,
        process_insurance,
        arq_func(index_knowledge_document, timeout=600),
        arq_func(process_channel_message, timeout=120),
        send_llm_budget_alert,
        send_llm_provider_billing_alert,
    ]

    # Bajas de miembros con fecha efectiva vencida. Cada 15 min y al arrancar el
    # worker (recupera las pendientes si estuvo parado). El corte inmediato lo
    # hace el middleware; esto lo persiste para quien no vuelve a entrar.
    cron_jobs: ClassVar[list[object]] = [
        cron(
            expire_member_removals,
            minute={0, 15, 30, 45},
            run_at_startup=True,
            unique=True,
        ),
        # Cambios de plan programados para el día 1 (D027). Cada hora y al
        # arrancar; hasta que corre, el plan nuevo ya rige por lectura.
        cron(
            apply_scheduled_plan_changes,
            minute={5},
            run_at_startup=True,
            unique=True,
        ),
    ]

    # Máximo de jobs ejecutándose simultáneamente en ESTE proceso worker.
    # No es el límite por tenant (eso lo gestiona invoice_slots.py con el
    # semáforo Redis). Con 5 slots globales y 5 por tenant, un único tenant
    # podría ocupar todos los slots del worker.
    max_jobs = 5

    # Timeout por job en segundos (3 minutos). El objetivo p95 de extracción
    # es <20 s (arquitectura.md §6), pero Instructor puede hacer hasta 3
    # intentos internos al LLM. 180 s cubre esos reintentos más latencia de
    # red y descarga de R2 sin matar jobs válidos que simplemente van lentos.
    job_timeout = 180

    # ARQ guarda el resultado del job en Redis durante esta cantidad de segundos
    # (1 hora). Pasado ese tiempo se elimina del cache. El polling de la UI
    # usa el estado de la BD (Invoice.status), no este resultado, así que
    # el TTL solo afecta a la inspección manual de jobs mediante arq CLI.
    keep_result = 3600

    # Número máximo de ejecuciones de un job. ARQ solo vuelve a ejecutar un job
    # ante arq.worker.Retry o si se cancela (worker reiniciado a mitad); una
    # excepción normal lo cierra como fallido sin repetir. Cada ejecución
    # cuenta, incluidas las diferidas por Retry. Los jobs de extracción no
    # repiten la llamada al LLM en la segunda ejecución (extraction_guard.py):
    # el tope por documento sigue siendo 1 + llm_extraction_max_retries (3).
    max_tries = 2

    # Logs sin datos personales también en el worker (Backlog P2c-2).
    on_startup = startup
