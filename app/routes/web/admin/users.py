"""SADM — gestión de usuarios (Paso 50)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import SuperAdmin, get_db_no_tenant
from app.services import admin_service

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/sadm/users", tags=["sadm"])


@router.post("", response_class=HTMLResponse)
async def create_user(
    request: Request,
    _admin: SuperAdmin,
    email: Annotated[str, Form()],
    tenant_id: Annotated[UUID, Form()],
    first_name: Annotated[str, Form()] = "",
    last_name: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "member",
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    await admin_service.create_user_in_org(
        db, email.strip(), first_name.strip(), last_name.strip(), tenant_id, role
    )
    members = await admin_service.list_tenant_members(db, tenant_id)
    return render(
        request,
        full="pages/sadm/organizations/members.html",
        partial="pages/sadm/organizations/_members.html",
        ctx={"members": members, "tenant_id": tenant_id},
    )


@router.delete("/{user_id}/orgs/{tenant_id}", response_class=HTMLResponse)
async def remove_member(
    request: Request,
    user_id: UUID,
    tenant_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    await admin_service.remove_user_from_org(db, user_id, tenant_id)
    members = await admin_service.list_tenant_members(db, tenant_id)
    return render(
        request,
        full="pages/sadm/organizations/members.html",
        partial="pages/sadm/organizations/_members.html",
        ctx={"members": members, "tenant_id": tenant_id},
    )
