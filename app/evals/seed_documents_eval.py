"""Siembra documentos conocidos en el tenant de evals para el eval de chat documental.

Los valores salen de los datasets de extracción (única fuente de verdad):

- facturas: ``invoices_v1`` sin casos de rechazo esperado ni los ficheros que
  ``tickets_v1`` reutiliza (serían la misma compra dos veces);
- tickets, contratos y pólizas: ``tickets_v1``, ``contracts_v1``, ``insurances_v1``;
  en campos ``any_of`` se toma la primera alternativa y en contratos la
  primera de ``partes`` como ``parte_contraria``.

Las filas se marcan con ``source_filename = "eval-docchat/<case_id>"`` y se
reemplazan en cada ejecución, sin tocar otros documentos del tenant.

Uso:
    infisical run -- uv run python -m app.evals.seed_documents_eval
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import delete

from app.core.db import session_factory_for_worker
from app.evals.eval_tenant import ensure_eval_tenant
from app.models import (
    Contract,
    ContractStatus,
    DocTypeCode,
    Insurance,
    InsuranceStatus,
    Invoice,
    InvoiceStatus,
    Ticket,
    TicketStatus,
)
from app.services import doc_type_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

DATASETS_DIR = Path(__file__).parent / "datasets"
SEED_PREFIX = "eval-docchat/"

SeedRow = dict[str, Any]


def _load(name: str) -> list[dict[str, Any]]:
    data = json.loads((DATASETS_DIR / name).read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = data["cases"]
    return cases


def _pick(spec: object) -> Any:
    """Valor canónico: el escalar o la primera alternativa de ``any_of`` (puede ser null)."""
    if isinstance(spec, dict) and "any_of" in spec:
        return spec["any_of"][0] if spec["any_of"] else None
    return spec


def _dec(spec: object) -> Decimal | None:
    value = _pick(spec)
    return None if value is None else Decimal(str(value))


def _date(spec: object) -> date | None:
    value = _pick(spec)
    return None if value is None else date.fromisoformat(str(value))


def _invoices(ticket_files: set[str]) -> list[SeedRow]:
    rows: list[SeedRow] = []
    for case in _load("invoices_v1.json"):
        gt = case.get("ground_truth") or {}
        if case.get("expected_rejection") or not gt.get("total"):
            continue
        if f"invoices/{case['file']}" in ticket_files:
            continue
        rows.append(
            {
                "case_id": case["id"],
                "fecha": _date(gt["fecha"]),
                "proveedor": gt["proveedor"],
                "cif_nif": (gt.get("cif_nif") or None),
                "base_imponible": _dec(gt.get("base_imponible")),
                "iva_amount": _dec(gt.get("iva_amount")),
                "total": _dec(gt["total"]),
            },
        )
    return rows


def _tickets() -> list[SeedRow]:
    return [
        {
            "case_id": case["id"],
            "file": case["file"],
            "fecha": _date(gt["fecha"]),
            "comercio": gt["comercio"],
            "numero_ticket": gt["numero_ticket"],
            "forma_pago": gt["forma_pago"],
            "base_imponible": _dec(gt.get("base_imponible")),
            "iva_percent": _dec(gt.get("iva_percent")),
            "iva_amount": _dec(gt.get("iva_amount")),
            "total": _dec(gt["total"]),
        }
        for case in _load("tickets_v1.json")
        for gt in [case["ground_truth"]]
    ]


def _contracts() -> list[SeedRow]:
    return [
        {
            "case_id": case["id"],
            "titulo": gt["titulo"],
            "numero_contrato": gt["numero_contrato"],
            "parte_contraria": gt["partes"][0]["nombre"],
            "cif_nif": gt["partes"][0]["cif_nif"],
            "fecha_inicio": _date(gt["fecha_inicio"]),
            "fecha_fin": _date(gt.get("fecha_fin")),
            "importe": _dec(gt.get("importe")),
        }
        for case in _load("contracts_v1.json")
        for gt in [case["ground_truth"]]
    ]


def _insurances() -> list[SeedRow]:
    return [
        {
            "case_id": case["id"],
            "aseguradora": gt["aseguradora"],
            "numero_poliza": gt["numero_poliza"],
            "tomador": gt["tomador"],
            "cif_nif": gt["cif_nif"],
            "tipo_seguro": gt["tipo_seguro"],
            "fecha_inicio": _date(gt["fecha_inicio"]),
            "fecha_fin": _date(gt.get("fecha_fin")),
            "prima": _dec(gt.get("prima")),
        }
        for case in _load("insurances_v1.json")
        for gt in [case["ground_truth"]]
    ]


def build_seed_documents() -> dict[str, list[SeedRow]]:
    """Documentos a sembrar por código de ``doc_types`` (función pura, sin BD)."""
    tickets = _tickets()
    return {
        DocTypeCode.factura.value: _invoices({row.pop("file") for row in tickets}),
        DocTypeCode.ticket.value: tickets,
        DocTypeCode.contrato.value: _contracts(),
        DocTypeCode.seguro.value: _insurances(),
    }


_MODELS: dict[str, tuple[type[Any], Any]] = {
    DocTypeCode.factura.value: (Invoice, InvoiceStatus.ready),
    DocTypeCode.ticket.value: (Ticket, TicketStatus.ready),
    DocTypeCode.contrato.value: (Contract, ContractStatus.ready),
    DocTypeCode.seguro.value: (Insurance, InsuranceStatus.ready),
}


async def _replace_rows(
    db: AsyncSession,
    tenant_id: UUID,
    code: str,
    rows: list[SeedRow],
) -> None:
    model, ready = _MODELS[code]
    await db.execute(
        delete(model).where(
            model.tenant_id == tenant_id,
            model.source_filename.like(f"{SEED_PREFIX}%"),
        ),
    )
    doc_type_id = await doc_type_service.get_doc_type_id(db, DocTypeCode(code))
    for row in rows:
        fields = {k: v for k, v in row.items() if k != "case_id"}
        db.add(
            model(
                tenant_id=tenant_id,
                doc_type_id=doc_type_id,
                status=ready,
                source_filename=f"{SEED_PREFIX}{row['case_id']}",
                confidence=Decimal("0.99"),
                **fields,
            ),
        )


async def seed(tenant_id: UUID | None = None) -> dict[str, int]:
    """Reemplaza los documentos sembrados del tenant de evals. Devuelve filas por tipo."""
    tid = await ensure_eval_tenant(tenant_id)
    documents = build_seed_documents()
    async with session_factory_for_worker(tid) as db:
        await doc_type_service.ensure_default_doc_types(db)
        for code, rows in documents.items():
            await _replace_rows(db, tid, code, rows)
        await db.commit()
    counts = {code: len(rows) for code, rows in documents.items()}
    logger.info("docchat_eval.seed_done", tenant_id=str(tid), **counts)
    return counts


if __name__ == "__main__":
    asyncio.run(seed())
