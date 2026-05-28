"""Rutas web del chat de consulta documental (módulo 1.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.db import session_scope, set_tenant_context
from app.core.errors import AppError
from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, RedisDep, get_db
from app.schemas.chat import ChatMessageListFilters, ChatThreadListFilters
from app.services import chat_service
from app.services.audit_service import AuditRequestContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def _audit_request_context(request: Request) -> AuditRequestContext:
    client = request.client
    ip = client.host if client else None
    return AuditRequestContext(ip=ip, user_agent=request.headers.get("user-agent"))


async def _chat_index_ctx(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    thread_id: UUID | None,
) -> dict[str, object]:
    threads_page = await chat_service.list_threads(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        filters=ChatThreadListFilters(),
    )
    active_thread = None
    messages = []
    if thread_id is not None:
        thread = await chat_service.get_thread(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            thread_id=thread_id,
        )
        active_thread = thread
        messages = await chat_service.list_messages(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            thread_id=thread_id,
            filters=ChatMessageListFilters(),
        )
    return {
        "threads": threads_page.items,
        "active_thread": active_thread,
        "active_thread_id": thread_id,
        "messages": messages,
    }


@router.get("")
@router.get("/")
async def chat_index(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    thread_id: UUID | None = None,
) -> HTMLResponse:
    """Página principal: sidebar de hilos + panel de mensajes."""
    ctx = await _chat_index_ctx(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread_id,
    )
    return render(request, full="pages/chat/index.html", ctx=ctx)


@router.get("/threads")
async def chat_threads_list(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    active_thread_id: UUID | None = None,
) -> HTMLResponse:
    """Fragmento HTMX: lista de hilos en sidebar."""
    page = await chat_service.list_threads(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        filters=ChatThreadListFilters(),
    )
    return render(
        request,
        full="components/chat_thread_list.html",
        partial="components/chat_thread_list.html",
        ctx={"threads": page.items, "active_thread_id": active_thread_id},
    )


@router.post("/threads")
async def chat_threads_create(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    title: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Crea un hilo vacío y devuelve el panel del hilo."""
    thread = await chat_service.create_thread(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        title=title,
    )
    response = render(
        request,
        full="components/chat_thread_panel_oob.html",
        partial="components/chat_thread_panel_oob.html",
        ctx={
            "thread": thread,
            "messages": [],
            "threads": (
                await chat_service.list_threads(
                    db,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    filters=ChatThreadListFilters(),
                )
            ).items,
            "active_thread_id": thread.id,
        },
    )
    response.headers["HX-Trigger"] = "chatThreadCreated"
    return response


@router.get("/threads/{thread_id}")
async def chat_thread_panel(
    request: Request,
    thread_id: UUID,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Fragmento HTMX: mensajes del hilo + composer."""
    thread = await chat_service.get_thread(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread_id,
    )
    messages = await chat_service.list_messages(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread_id,
        filters=ChatMessageListFilters(),
    )
    return render(
        request,
        full="components/chat_thread_panel.html",
        partial="components/chat_thread_panel.html",
        ctx={"thread": thread, "messages": messages},
    )


@router.post("/threads/{thread_id}/messages")
async def chat_post_message(
    request: Request,
    thread_id: UUID,
    user: CurrentUser,
    tenant: CurrentTenant,
    redis_conn: RedisDep,
    db: AsyncSession = Depends(get_db),
    content: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Persiste mensaje usuario y devuelve fragmento con conector SSE."""
    user_message = await chat_service.post_user_message(
        db,
        redis_conn,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread_id,
        content=content,
        request_ctx=_audit_request_context(request),
    )
    thread = await chat_service.get_thread(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread_id,
    )
    threads_page = await chat_service.list_threads(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        filters=ChatThreadListFilters(),
    )
    response = render(
        request,
        full="components/chat_message_post_oob.html",
        partial="components/chat_message_post_oob.html",
        ctx={
            "thread": thread,
            "user_message": user_message,
            "threads": threads_page.items,
            "active_thread_id": thread_id,
        },
    )
    response.headers["HX-Trigger"] = "chatMessageSent"
    return response


@router.get("/threads/{thread_id}/stream")
async def chat_stream_assistant(
    thread_id: UUID,
    user: CurrentUser,
    tenant: CurrentTenant,
    message_id: Annotated[UUID, Query()],
) -> EventSourceResponse:
    """SSE: tokens acumulados de la respuesta assistant tras POST del mensaje."""

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        accumulated = ""
        try:
            async with session_scope() as db:
                await set_tenant_context(db, str(tenant.id))
                async for chunk in chat_service.stream_reply(
                    db,
                    tenant_id=tenant.id,
                    user_id=user.id,
                    thread_id=thread_id,
                    user_message_id=message_id,
                ):
                    accumulated += chunk
                    yield {"event": "message", "data": accumulated}
            yield {"event": "close", "data": ""}
        except AppError as exc:
            logger.warning(
                "chat.stream_app_error",
                thread_id=str(thread_id),
                message_id=str(message_id),
                code=exc.code,
                message=exc.message,
            )
            yield {"event": "error", "data": exc.message}
            yield {"event": "close", "data": ""}
        except Exception:
            logger.exception(
                "chat.stream_failed",
                thread_id=str(thread_id),
                message_id=str(message_id),
                tenant_id=str(tenant.id),
            )
            yield {
                "event": "error",
                "data": "Ha ocurrido un error al procesar la consulta.",
            }
            yield {"event": "close", "data": ""}

    return EventSourceResponse(event_generator())


@router.get("/threads/{thread_id}/messages/{user_message_id}/assistant")
async def chat_assistant_message_fragment(
    request: Request,
    thread_id: UUID,
    user_message_id: UUID,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Fragmento HTMX: burbuja assistant con citas tras completar el SSE."""
    message = await chat_service.get_assistant_message_after_user(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread_id,
        user_message_id=user_message_id,
    )
    if message is None:
        return HTMLResponse(content="", status_code=204)
    return render(
        request,
        full="components/chat_message_assistant.html",
        partial="components/chat_message_assistant.html",
        ctx={"message": message},
    )
