from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.templating import render

router = APIRouter(tags=["demo"])


@router.get("/demo")
async def demo_page(request: Request) -> HTMLResponse:
    return render(request, full="pages/demo.html")


@router.post("/demo/htmx")
async def demo_htmx(request: Request) -> HTMLResponse:
    return render(
        request,
        full="components/htmx_demo.html",
        partial="components/htmx_demo.html",
        ctx={"now": datetime.now().strftime("%H:%M:%S")},
    )
