from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templating import render
from app.deps import CurrentUser

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/")
async def settings_index(request: Request, user: CurrentUser) -> HTMLResponse:
    return render(request, full="pages/settings/index.html", ctx={"user": user})
