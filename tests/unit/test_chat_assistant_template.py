"""Plantilla assistant del chat unificado (Paso 20 E)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.core.templating import templates
from app.models import ChatMessageRole
from app.schemas.chat import ChatCitation, ChatMessageRead


def test_chat_message_assistant_renders_content_without_inline_refs() -> None:
    """Contenido del asistente sin refs ``[N]`` inline (bloque de citas deshabilitado temporalmente)."""
    chunk_id = uuid4()
    doc_id = uuid4()
    msg_id = uuid4()
    thread_id = uuid4()
    tenant_id = uuid4()
    now = datetime.now(tz=UTC)
    message = ChatMessageRead(
        id=msg_id,
        thread_id=thread_id,
        tenant_id=tenant_id,
        role=ChatMessageRole.assistant,
        content="Abrimos de 9 a 18 [1].",
        citations=[
            ChatCitation(
                ref=1,
                chunk_id=chunk_id,
                document_id=doc_id,
                document_name="FAQ Horarios",
                kind="schedule",
                position=0,
                content_snippet="Lunes a viernes 9-18h",
                score=0.88,
            ),
        ],
        created_at=now,
    )
    tpl = templates.get_template("components/chat_message_assistant.html")
    html = tpl.render(message=message)
    assert "Abrimos de 9 a 18" in html
    assert "[1]" not in html
    assert "citations-block" not in html
    assert "FAQ Horarios" not in html
