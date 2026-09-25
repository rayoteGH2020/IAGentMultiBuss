"""Configuración del worker ARQ.

ARQ lee esta clase al arrancar el proceso worker:
    uv run arq app.jobs.settings.WorkerSettings
Todos los atributos son leídos como atributos de clase, no de instancia.
"""

from typing import ClassVar

from arq import func as arq_func
from arq.connections import RedisSettings

from app.config import get_settings
from app.jobs.channel_jobs import process_channel_message
from app.jobs.contract_jobs import process_contract
from app.jobs.insurance_jobs import process_insurance
from app.jobs.invoice_jobs import process_invoice
from app.jobs.knowledge_jobs import index_knowledge_document
from app.jobs.ticket_jobs import process_ticket


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

    # Número máximo de intentos antes de que ARQ marque el job como fallido.
    # Con max_tries=2, si el job lanza una excepción no controlada (distinto
    # de arq.worker.Retry), ARQ lo reintenta una vez más automáticamente.
    # Combinado con max_retries=2 de Instructor, en el peor caso se harían
    # hasta 2 x 3 = 6 llamadas al LLM antes de agotar todos los intentos.
    max_tries = 2
