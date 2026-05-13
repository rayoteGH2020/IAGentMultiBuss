from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templating import render

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/")
async def settings_index(request: Request) -> HTMLResponse:
    return render(request, full="pages/settings/index.html")
