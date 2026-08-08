"""End-to-end over the real document routes, real extractors, SQLite session.

The properties under test are the ones the ledger depends on: a document that yielded
nothing is never stored, identical bytes never become two documents, a document is
withdrawn rather than deleted so citations against it keep resolving, and every chunk
carries a locator that can be rendered as one highlight.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import documents as documents_route
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.db.base_class import Base
from app.db.session import get_db
from app.models.document import Document, DocumentChunk
from app.models.scope import ScopeNode
from tests.document_fixtures import (
    ruled_table_pdf,
    scanned_pdf,
    text_pdf,
    word_document,
    workbook,
)

pytestmark = pytest.mark.asyncio

SCOPE_ID = 1
PDF = text_pdf([["Permit validity"], ["The consent is valid for ninety days."]])


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'d.db'}", future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as session:
        session.add(
            ScopeNode(
                id=SCOPE_ID,
                kind="project",
                name="Terminal",
                is_default=True,
                created_by="test",
            )
        )
        await session.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(documents_route.router)

    async def override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac._maker = Session  # type: ignore[attr-defined]
        yield ac
    await engine.dispose()


async def _upload(client, data: bytes, filename: str):
    return await client.post(
        "/documents",
        files={"file": (filename, data, "application/octet-stream")},
        headers={"X-Actor": "Dana"},
    )


class TestUpload:
    async def test_a_pdf_lands_with_chunks_and_a_page_count(self, client) -> None:
        response = await _upload(client, PDF, "permit.pdf")
        assert response.status_code == 201
        body = response.json()
        assert body["created"] is True
        document = body["document"]
        assert document["suffix"] == ".pdf"
        assert document["source_kind"] == "upload"
        assert document["page_count"] == 2
        assert document["chunk_count"] > 0
        assert document["uploaded_by"] == "Dana"
        assert document["status"] == "active"
        assert len(document["sha256"]) == 64

    async def test_every_chunk_carries_a_locator(self, client) -> None:
        """A citation that cannot be rendered as one highlight is not evidence."""
        created = (await _upload(client, PDF, "permit.pdf")).json()["document"]
        chunks = (await client.get(f"/documents/{created['id']}/chunks")).json()
        assert chunks
        for chunk in chunks:
            assert chunk["locator"] is not None
            assert "page" in chunk["locator"]
            assert chunk["char_count"] == len(chunk["text"])

    async def test_chunks_come_back_in_document_order(self, client) -> None:
        created = (await _upload(client, PDF, "permit.pdf")).json()["document"]
        chunks = (await client.get(f"/documents/{created['id']}/chunks")).json()
        assert [c["ordinal"] for c in chunks] == list(range(len(chunks)))

    async def test_identical_bytes_do_not_become_a_second_document(self, client) -> None:
        """Two copies of one source double its weight in every retrieval over the text."""
        first = await _upload(client, PDF, "permit.pdf")
        second = await _upload(client, PDF, "permit-copy.pdf")
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert second.json()["document"]["id"] == first.json()["document"]["id"]
        assert len((await client.get("/documents")).json()) == 1

    async def test_a_scan_is_refused_and_nothing_is_stored(self, client) -> None:
        response = await _upload(client, scanned_pdf(), "scan.pdf")
        assert response.status_code == 422
        assert response.json()["error"] == "document_has_no_text"
        assert (await client.get("/documents")).json() == []

    async def test_an_unsupported_format_is_refused(self, client) -> None:
        response = await _upload(client, b"binary", "drawing.dwg")
        assert response.status_code == 415
        assert ".pdf" in response.json()["detail"]

    async def test_an_empty_upload_is_refused(self, client) -> None:
        assert (await _upload(client, b"", "empty.pdf")).status_code == 422

    async def test_a_corrupt_file_is_refused(self, client) -> None:
        response = await _upload(client, b"%PDF-1.4 nope", "broken.pdf")
        assert response.status_code == 422
        assert response.json()["error"] == "document_unreadable"

    async def test_a_word_document_keeps_its_section_paths(self, client) -> None:
        data = word_document(
            [
                ("heading1", "Consents"),
                ("heading2", "Validity"),
                ("body", "The consent is valid for ninety days from issue."),
            ]
        )
        created = (await _upload(client, data, "spec.docx")).json()["document"]
        chunks = (await client.get(f"/documents/{created['id']}/chunks")).json()
        assert any(c["section"] == "Consents › Validity" for c in chunks)

    async def test_a_workbook_becomes_labelled_rows(self, client) -> None:
        data = workbook({"Costs": [["Package", "Value"], ["Civils", 2000000]]})
        created = (await _upload(client, data, "estimate.xlsx")).json()["document"]
        chunks = (await client.get(f"/documents/{created['id']}/chunks")).json()
        assert chunks[0]["kind"] == "table_row"
        assert chunks[0]["text"] == "Package: Civils | Value: 2000000"
        assert chunks[0]["locator"]["cells"] == "A2:B2"

    async def test_extraction_warnings_land_on_the_document(self, client) -> None:
        """A hole in the corpus is declared on the record, not written to a log."""
        data = workbook({"Costs": [["P", "V"], ["Civils", 1]], "Notes": []})
        created = (await _upload(client, data, "e.xlsx")).json()["document"]
        assert any("Notes" in w for w in created["warnings"])


class TestChunkFiltering:
    async def test_filtering_by_kind(self, client) -> None:
        data = ruled_table_pdf(
            ["Consent register"], [["Consent", "Days"], ["Env permit", "90"]]
        )
        created = (await _upload(client, data, "r.pdf")).json()["document"]
        rows = (
            await client.get(f"/documents/{created['id']}/chunks", params={"kind": "table_row"})
        ).json()
        assert rows
        assert all(c["kind"] == "table_row" for c in rows)

    async def test_chunks_of_a_missing_document_are_a_404(self, client) -> None:
        assert (await client.get("/documents/999/chunks")).status_code == 404


class TestPaste:
    async def test_pasted_text_becomes_a_document(self, client) -> None:
        response = await client.post(
            "/documents/paste",
            json={
                "title": "Crossing standard",
                "text": "# Cover\nMinimum cover is 1.2 m in agricultural land.",
                "source_url": "https://example.org/standard",
            },
        )
        assert response.status_code == 201
        document = response.json()["document"]
        assert document["source_kind"] == "paste"
        assert document["title"] == "Crossing standard"
        assert "https://example.org/standard" in document["filename"]

    async def test_the_same_paste_twice_is_one_document(self, client) -> None:
        body = {"title": "Note", "text": "The consent lapses after ninety days."}
        assert (await client.post("/documents/paste", json=body)).status_code == 201
        assert (await client.post("/documents/paste", json=body)).status_code == 200

    async def test_empty_text_is_refused_by_the_boundary(self, client) -> None:
        response = await client.post(
            "/documents/paste", json={"title": "Note", "text": ""}
        )
        assert response.status_code == 422


class TestWithdrawal:
    async def test_there_is_no_delete_route(self, client) -> None:
        """A chunk id can sit in a proposal's evidence_refs with no FK behind it."""
        created = (await _upload(client, PDF, "permit.pdf")).json()["document"]
        assert (await client.delete(f"/documents/{created['id']}")).status_code == 405

    async def test_withdrawing_keeps_every_chunk(self, client) -> None:
        created = (await _upload(client, PDF, "permit.pdf")).json()["document"]
        before = len((await client.get(f"/documents/{created['id']}/chunks")).json())
        response = await client.post(
            f"/documents/{created['id']}/withdraw", json={"reason": "Superseded by rev C"}
        )
        assert response.json()["status"] == "withdrawn"
        assert response.json()["withdrawn_reason"] == "Superseded by rev C"
        after = len((await client.get(f"/documents/{created['id']}/chunks")).json())
        assert after == before

    async def test_restoring_clears_the_reason(self, client) -> None:
        created = (await _upload(client, PDF, "permit.pdf")).json()["document"]
        await client.post(f"/documents/{created['id']}/withdraw", json={"reason": "x"})
        body = (await client.post(f"/documents/{created['id']}/restore")).json()
        assert body["status"] == "active"
        assert body["withdrawn_reason"] is None

    async def test_the_summary_separates_active_from_withdrawn(self, client) -> None:
        first = (await _upload(client, PDF, "permit.pdf")).json()["document"]
        await _upload(client, text_pdf([["Another document entirely."]]), "b.pdf")
        await client.post(f"/documents/{first['id']}/withdraw", json={})
        summary = (await client.get("/documents/corpus/summary")).json()
        assert summary["active"]["documents"] == 1
        assert summary["withdrawn"]["documents"] == 1
        assert summary["active"]["chunks"] > 0


class TestListing:
    async def test_newest_first(self, client) -> None:
        await _upload(client, PDF, "first.pdf")
        second = (
            await _upload(client, text_pdf([["Second document."]]), "second.pdf")
        ).json()["document"]
        rows = (await client.get("/documents")).json()
        assert rows[0]["id"] == second["id"]

    async def test_filtering_by_status(self, client) -> None:
        created = (await _upload(client, PDF, "permit.pdf")).json()["document"]
        await _upload(client, text_pdf([["Second document."]]), "second.pdf")
        await client.post(f"/documents/{created['id']}/withdraw", json={})
        rows = (await client.get("/documents", params={"status": "active"})).json()
        assert [r["filename"] for r in rows] == ["second.pdf"]

    async def test_an_unknown_status_filter_is_refused(self, client) -> None:
        assert (
            await client.get("/documents", params={"status": "archived"})
        ).status_code == 422

    async def test_scope_filtering_rolls_up(self, client) -> None:
        async with client._maker() as session:  # type: ignore[attr-defined]
            session.add(
                ScopeNode(id=10, kind="portfolio", name="Capital", created_by="test")
            )
            await session.flush()
            await session.execute(
                sa.update(ScopeNode).where(ScopeNode.id == SCOPE_ID).values(parent_id=10)
            )
            await session.commit()

        await _upload(client, PDF, "permit.pdf")
        assert len((await client.get("/documents", params={"scope_id": 10})).json()) == 1
        assert len((await client.get("/documents", params={"scope_id": 1})).json()) == 1

    async def test_formats_advertises_what_can_be_uploaded(self, client) -> None:
        body = (await client.get("/documents/formats")).json()
        assert ".pdf" in body["suffixes"]
        assert ".xer" not in body["suffixes"]
        assert body["max_bytes"] > 0


class TestReextraction:
    async def test_reextraction_replaces_the_chunk_set(self, client) -> None:
        """Ordinals are a sequence with no gaps; a partial update has nothing stable to
        match new text against, because text moves when a paragraph above it is edited."""
        from app.services import document_ingest

        created = (await _upload(client, PDF, "permit.pdf")).json()["document"]
        async with client._maker() as session:  # type: ignore[attr-defined]
            document = await session.get(Document, created["id"])
            await document_ingest.reextract(session, document, data=PDF)
            await session.commit()

        async with client._maker() as session:  # type: ignore[attr-defined]
            rows = (
                await session.scalars(
                    sa.select(DocumentChunk)
                    .where(DocumentChunk.document_id == created["id"])
                    .order_by(DocumentChunk.ordinal)
                )
            ).all()
        assert [c.ordinal for c in rows] == list(range(len(rows)))
        assert len(rows) == created["chunk_count"]
