"""SADM — gestión de organizaciones (Paso 50)."""

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

router = APIRouter(prefix="/sadm/organizations", tags=["sadm"])


@router.get("", response_class=HTMLResponse)
async def list_organizations(
    request: Request,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenants = await admin_service.list_all_tenants(db)
    return render(
        request,
        full="pages/sadm/organizations/index.html",
        partial="pages/sadm/organizations/_list.html",
        ctx={"tenants": tenants},
    )


@router.post("", response_class=HTMLResponse)
async def create_organization(
    request: Request,
    _admin: SuperAdmin,
    name: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenant = await admin_service.create_org_with_tenant(db, name.strip())
    tenants = await admin_service.list_all_tenants(db)
    return render(
        request,
        full="pages/sadm/organizations/index.html",
        partial="pages/sadm/organizations/_list.html",
        ctx={"tenants": tenants, "created": tenant},
    )


@router.get("/{tenant_id}/members", response_class=HTMLResponse)
async def list_members(
    request: Request,
    tenant_id: UUID,
    _admin: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    members = await admin_service.list_tenant_members(db, tenant_id)
    return render(
        request,
        full="pages/sadm/organizations/members.html",
        partial="pages/sadm/organizations/_members.html",
        ctx={"members": members, "tenant_id": tenant_id},
    )
