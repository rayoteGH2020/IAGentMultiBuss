from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, get_db
from app.services import invoice_service

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("")
async def invoices_index(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    invoices = await invoice_service.list_invoices(db, tenant.id)
    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_table.html",
        ctx={"invoices": invoices, "user": user, "tenant": tenant},
    )
