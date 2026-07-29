"""API JSON del motor de huecos (Paso 30 Fase D.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from app.deps import CurrentTenant, RequireAppointmentCreateOrEdit, get_db
from app.schemas.scheduling import FindSlotsRequest, FindSlotsResponse
from app.services import appointment_slot_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])


@router.post(
    "/find-slots",
    response_model=FindSlotsResponse,
    summary="Buscar próximos huecos disponibles",
)
async def find_slots(
    payload: FindSlotsRequest,
    tenant: CurrentTenant,
    _: RequireAppointmentCreateOrEdit,
    db: AsyncSession = Depends(get_db),
) -> FindSlotsResponse:
    """Devuelve hasta N huecos libres para un servicio (sin LLM)."""
    return await appointment_slot_service.find_next_available_slots(
        db,
        tenant.id,
        payload,
    )
