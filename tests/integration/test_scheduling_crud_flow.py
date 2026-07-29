"""Flujo CRUD HTTP de citas con BD real (Paso 30 §E.4 / #12)."""

from __future__ import annotations

import pytest
from app.main import create_app
from app.schemas.scheduling import FindSlotsRequest
from app.services import appointment_slot_service, internal_appointment_service
from httpx import ASGITransport, AsyncClient

from tests.integration.scheduling_test_helpers import (
    FIXED_SCHEDULING_NOW,
    auth_headers,
    csrf_headers,
    fake_clerk_resolve_factory,
    future_appointment_start,
    patch_scheduling_now,
    seed_committed_scheduling_tenant,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_scheduling_crud_http_flow(
    scheduling_schema_ready: None,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alta → calendario → cancelación → hueco disponible en find-slots."""
    patch_scheduling_now(monkeypatch, "app.services.internal_appointment_service")
    patch_scheduling_now(monkeypatch, "app.services.appointment_slot_service")

    seed = await seed_committed_scheduling_tenant(rls_database_url)
    start = future_appointment_start(hour=10, minute=0)
    client_name = "Cliente CRUD Test"

    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        fake_clerk_resolve_factory(seed.tenant, seed.user, seed.membership),
    )

    app = create_app()
    headers = {**auth_headers(), **csrf_headers(seed.user, seed.tenant)}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post(
            "/appointments",
            headers=headers,
            data={
                "service_id": str(seed.service_id),
                "professional_id": str(seed.professional_id),
                "appointment_date": start.date().isoformat(),
                "start_time": start.strftime("%H:%M"),
                "client_name": client_name,
                "client_phone": "612345678",
            },
        )
        assert create_resp.status_code == 200
        assert client_name in create_resp.text
        assert create_resp.headers.get("HX-Trigger") == "appointmentChanged"

        calendar_resp = await client.get(
            f"/appointments/calendar?view=day&date={start.date().isoformat()}",
            headers=auth_headers(),
        )
        assert calendar_resp.status_code == 200
        assert client_name in calendar_resp.text

        from app.core.db import set_tenant_context
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(rls_database_url, poolclass=NullPool)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            await set_tenant_context(session, str(seed.tenant_id))
            rows = await internal_appointment_service.list_appointments(
                session,
                seed.tenant_id,
                range_start=FIXED_SCHEDULING_NOW,
                range_end=start.replace(hour=23, minute=59),
            )
            assert len(rows) == 1
            appointment_id = rows[0].id

        cancel_resp = await client.post(
            f"/appointments/{appointment_id}/cancel",
            headers=headers,
            data={"cancellation_reason": "Test cancel"},
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.headers.get("HX-Trigger") == "appointmentChanged"

        async with sm() as session:
            await set_tenant_context(session, str(seed.tenant_id))
            slots = await appointment_slot_service.find_next_available_slots(
                session,
                seed.tenant_id,
                FindSlotsRequest(
                    service_id=seed.service_id,
                    professional_id=seed.professional_id,
                    after=start,
                    count=3,
                ),
            )
            assert any(slot.start == start for slot in slots.slots)

    await engine.dispose()
