from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, get_db
from app.services import invoice_service

router = APIRouter(tags=["web"])


@router.get("/")
async def home(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    invoice_stats = await invoice_service.get_invoice_date_stats(db, tenant.id)
    return render(
        request,
        full="pages/home/index.html",
        ctx={
            "user": user,
            "tenant": tenant,
            "invoice_stats": invoice_stats,
        },
    )
