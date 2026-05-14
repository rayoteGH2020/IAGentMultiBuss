from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser

router = APIRouter(tags=["web"])


@router.get("/")
async def home(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
) -> HTMLResponse:
    return render(request, full="pages/home/index.html", ctx={"user": user, "tenant": tenant})
