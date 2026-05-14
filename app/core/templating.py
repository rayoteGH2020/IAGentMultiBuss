from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


def _inject_auth_context(request: Request) -> dict[str, Any]:
    return {
        "user": getattr(request.state, "user", None),
        "tenant": getattr(request.state, "tenant", None),
        "membership": getattr(request.state, "membership", None),
    }


def render(
    request: Request,
    *,
    full: str,
    partial: str | None = None,
    ctx: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """
    Renderiza una página completa o un fragmento según el header HX-Request.

    - Sin HX-Request: usa `full` (página completa con layout).
    - Con HX-Request: usa `partial` (fragmento HTML).
    - Si `partial` no se pasa, siempre usa `full`.
    """
    ctx = {**_inject_auth_context(request), **(ctx or {})}
    is_htmx = request.headers.get("HX-Request") == "true"
    template = partial if (is_htmx and partial) else full
    return templates.TemplateResponse(
        request=request,
        name=template,
        context=ctx,
        status_code=status_code,
    )
