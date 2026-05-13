from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templating import render
from app.deps import CurrentUser

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("/")
async def invoices_index(request: Request, user: CurrentUser) -> HTMLResponse:
    return render(request, full="pages/invoices/index.html", ctx={"user": user})
