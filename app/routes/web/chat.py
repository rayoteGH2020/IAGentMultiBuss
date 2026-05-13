from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templating import render

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/")
async def chat_index(request: Request) -> HTMLResponse:
    return render(request, full="pages/chat/index.html")
