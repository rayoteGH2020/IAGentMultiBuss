"""Tests de filtrado de citas RAG por existencia en tenant."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.schemas.chat import ChatCitation
from app.services.chat_citations import filter_citations_existing_for_tenant, finalize_citations


def test_finalize_citations_assigns_refs() -> None:
    cites = [
        ChatCitation(
            ref=99,
            chunk_id=uuid4(),
            document_id=uuid4(),
            document_name="A",
            kind="policy",
            position=0,
            content_snippet="hola",
            score=0.2,
        ),
        ChatCitation(
            ref=1,
            chunk_id=uuid4(),
            document_id=uuid4(),
            document_name="B",
            kind="policy",
            position=1,
            content_snippet="adios",
            score=0.9,
        ),
    ]
    out = finalize_citations(cites)
    assert out[0].document_name == "B"
    assert out[0].ref == 1
    assert out[1].ref == 2


@pytest.mark.asyncio
async def test_filter_citations_drops_unknown_chunks(
    knowledge_schema_ready: None,
    db_session,
    tenant_factory,
) -> None:
    from app.core.db import set_tenant_context
    from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
    from app.schemas.knowledge import KnowledgeDocumentKind, KnowledgeDocumentStatus

    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    doc = KnowledgeDocument(
        tenant_id=tenant.id,
        kind=KnowledgeDocumentKind.policy,
        status=KnowledgeDocumentStatus.ready,
        name="Politica",
        original_filename="p.md",
        source_file_key=f"tenants/{tenant.id}/p.md",
        source_mime="text/markdown",
        file_size_bytes=12,
    )
    db_session.add(doc)
    await db_session.flush()

    embedding = [0.0] * 512
    embedding[0] = 1.0
    chunk = KnowledgeChunk(
        tenant_id=tenant.id,
        document_id=doc.id,
        position=0,
        content="contenido",
        embedding=embedding,
    )
    db_session.add(chunk)
    await db_session.flush()

    good = ChatCitation(
        ref=1,
        chunk_id=chunk.id,
        document_id=doc.id,
        document_name="Politica",
        kind="policy",
        position=0,
        content_snippet="contenido",
        score=0.5,
    )
    bad = ChatCitation(
        ref=2,
        chunk_id=uuid4(),
        document_id=doc.id,
        document_name="Fantasma",
        kind="policy",
        position=1,
        content_snippet="no existe",
        score=0.9,
    )

    filtered = await filter_citations_existing_for_tenant(
        db_session,
        tenant_id=tenant.id,
        citations=[good, bad],
    )
    assert len(filtered) == 1
    assert filtered[0].chunk_id == chunk.id
    assert filtered[0].ref == 1
