from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templating import render

router = APIRouter(tags=["web"])


@router.get("/")
async def home(request: Request) -> HTMLResponse:
    return render(request, full="pages/home/index.html")
