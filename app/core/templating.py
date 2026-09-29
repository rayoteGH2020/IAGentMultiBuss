import time
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.core.calendar_datetime import (
    calendar_event_date_chip,
    calendar_event_is_all_day,
    calendar_event_weekday,
    format_calendar_event_time,
    google_iso_to_local_input,
)
from app.core.chat_content import chat_content_plain
from app.core.clerk_frontend import clerk_browser_context
from app.core.csrf import csrf_tenant_id_for_request, generate_csrf_token
from app.core.currency_display import currency_symbol
from app.core.datetime_display import local_datetime
from app.core.document_processing_errors import format_user_processing_error
from app.core.entitlement_codes import plan_ui_name
from app.core.permissions import (
    home_path_for_role,
    is_manager_role,
    is_org_admin_role,
    membership_can_appointment,
    nav_items_for_access,
    nav_items_for_role,
    role_label,
)
from app.core.scheduling_granularity import slot_minute_options
from app.core.scheduling_ui import format_range_label
from app.schemas.scheduling import sanitize_professional_color

# Instancia a nivel de módulo: Jinja2 cachea las plantillas compiladas en
# memoria. Crear una nueva instancia por request recompilaria las plantillas
# en cada llamada, lo que sería muy costoso.
# La ruta es relativa al directorio de trabajo (raíz del proyecto), desde
# donde se lanza uvicorn.
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["local_datetime"] = local_datetime
templates.env.filters["currency_symbol"] = currency_symbol
templates.env.filters["calendar_event_time"] = format_calendar_event_time
templates.env.filters["calendar_local_input"] = google_iso_to_local_input
templates.env.filters["calendar_event_date_chip"] = calendar_event_date_chip
templates.env.filters["calendar_event_is_all_day"] = calendar_event_is_all_day
templates.env.filters["calendar_event_weekday"] = calendar_event_weekday


def _user_processing_error_filter(raw_error: str | None, filename: str | None = None) -> str:
    return format_user_processing_error(raw_error, filename=filename)


templates.env.filters["user_processing_error"] = _user_processing_error_filter
templates.env.filters["chat_content_plain"] = chat_content_plain
templates.env.filters["scheduling_range_label"] = lambda view, start, end: format_range_label(
    view, start, end
)
templates.env.filters["professional_color"] = sanitize_professional_color
templates.env.filters["slot_minute_options"] = slot_minute_options
templates.env.globals["membership_can_appointment"] = membership_can_appointment
templates.env.globals["nav_items_for_role"] = nav_items_for_role
templates.env.globals["nav_items_for_access"] = nav_items_for_access
templates.env.globals["home_path_for_role"] = home_path_for_role
templates.env.globals["is_manager_role"] = is_manager_role
templates.env.globals["is_org_admin_role"] = is_org_admin_role
templates.env.filters["role_label"] = role_label
# Nombre del plan en cualquier plantilla: {{ tenant.plan_code | plan_label }}.
templates.env.filters["plan_label"] = plan_ui_name

# Marca de tiempo del arranque del proceso: usada como query param de
# cache-busting en /static (ver static_asset() más abajo).
_STATIC_ASSET_VERSION = str(int(time.time()))


def static_asset(path: str) -> str:
    """Añade `?v=<arranque del proceso>` a una ruta /static.

    Con hx-boost, la navegación entre páginas no vuelve a ejecutar las
    etiquetas <script>/<link> del <head>; y sin este parámetro, el navegador
    puede seguir sirviendo una versión cacheada de un asset ya cambiado en
    disco (p. ej. tras un redeploy) hasta que el usuario fuerce un refresco
    completo. Cambiar el query param en cada arranque invalida esa caché.
    """
    return f"{path}?v={_STATIC_ASSET_VERSION}"


templates.env.globals["static_asset"] = static_asset


def _inject_auth_context(request: Request) -> dict[str, Any]:
    # getattr con default None en lugar de request.state.user directamente:
    # request.state es un objeto dinámico (SimpleNamespace); acceder a un
    # atributo no existente lanzaría AttributeError. En rutas sin auth
    # (health, assets estáticos) estos atributos pueden no estar seteados.
    user = getattr(request.state, "user", None)
    tenant = getattr(request.state, "tenant", None)
    csrf_token = ""
    if user is not None:
        tenant_for_csrf = csrf_tenant_id_for_request(
            tenant_id=tenant.id if tenant is not None else None,
            missing_organization=bool(getattr(request.state, "auth_missing_organization", False)),
        )
        if tenant_for_csrf is not None:
            csrf_token = generate_csrf_token(user_id=user.id, tenant_id=tenant_for_csrf)
    ctx: dict[str, Any] = {
        "user": user,
        "tenant": tenant,
        "membership": getattr(request.state, "membership", None),
        "entitlements": getattr(request.state, "entitlements", None),
        "csrf_token": csrf_token,
        # Aviso de cupo de IA agotado (banner en layouts/dashboard.html).
        "llm_budget_exhausted_notice": (
            get_settings().llm_budget_exhausted_notice
            if getattr(request.state, "llm_budget_exhausted", False)
            else None
        ),
    }
    if user is not None:
        # clerk-js en el panel para renovar la cookie __session (layouts/dashboard.html).
        # Las páginas de auth pasan su propio contexto de Clerk y lo sobrescriben.
        ctx.update(clerk_browser_context(get_settings()))
    return ctx


def render(
    request: Request,
    *,  # Todos keyword-only: evita confundir full/partial/ctx por posición.
    full: str,
    partial: str | None = None,
    ctx: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Renderiza una página completa o un fragmento según el header HX-Request.

    Implementa el patrón página/fragmento de Agents.md §6:
    - Sin HX-Request (visita directa, F5, deep link): devuelve `full` con
      el layout completo (sidebar, nav, etc.) para que la URL funcione sola.
    - Con HX-Request (navegación HTMX interna): devuelve `partial` para
      intercambiar solo el fragmento relevante del DOM.
    - Si `partial` es None, siempre devuelve `full` (endpoints con una sola
      plantilla que sirve para ambos contextos).
    """
    # El contexto de auth se inyecta automáticamente en todas las plantillas
    # para que el layout pueda mostrar el nombre del usuario, el plan del
    # tenant, etc. sin que cada endpoint lo pase explícitamente.
    # Los valores del caller (ctx) van al final para poder sobreescribir
    # los del auth context si algún endpoint lo necesitase.
    ctx = {**_inject_auth_context(request), **(ctx or {})}

    is_htmx = request.headers.get("HX-Request") == "true"
    # HX-Boosted: hx-boost intercepta los clics en enlaces normales y los
    # convierte en peticiones HTMX (envía HX-Request: true), pero el servidor
    # debe responder con la página completa para que hx-boost pueda actualizar
    # solo el <body> sin romper el layout. Sin esta detección, boost recibiría
    # el fragmento y reemplazaría el body con contenido incompleto.
    is_boosted = request.headers.get("HX-Boosted") == "true"

    # para una condición con tres variables booleanas.
    if is_htmx and partial is not None and not is_boosted:  # noqa: SIM108
        template = partial
    else:
        template = full

    return templates.TemplateResponse(
        request=request,
        name=template,
        context=ctx,
        status_code=status_code,
    )
