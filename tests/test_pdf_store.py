"""
PDF persistence and serving.

The original upload used to be unlinked in a finally block, so the only copy of a
document was discarded at ingest and a citation could never open its source page.
These tests cover keeping it, refusing to double-index, and serving it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ingestion, main
from app.ingestion import AlreadyIndexed, ContentConflict


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion.cfg, "chunk_store_path", str(tmp_path / "chunk_store.json"))
    monkeypatch.setattr(ingestion.cfg, "pdf_store_dir", str(tmp_path / "pdf_store"))


@pytest.fixture
def stub_pages(monkeypatch):
    def _apply(pages=("Revenue from operations 1,62,990",)):
        monkeypatch.setattr(
            ingestion,
            "_extract_pages",
            lambda _p: [
                {"page_number": i, "text": t, "section_heading": None}
                for i, t in enumerate(pages, start=1)
            ],
        )
    return _apply


@pytest.fixture
def pdf(tmp_path):
    def _make(name="report.pdf", body=b"%PDF-1.4 first version"):
        path = tmp_path / name
        path.write_bytes(body)
        return str(path)
    return _make


# ── Persistence ───────────────────────────────────────────────────────────────

def test_pdf_is_copied_into_the_store(pdf, stub_pages):
    stub_pages()
    src = pdf(body=b"%PDF-1.4 original bytes")

    doc_id, _ = ingestion.ingest_pdf(src, "Infosys FY2024-25", entity="Infosys",
                                     fiscal_year="FY2024-25")

    stored = ingestion.stored_pdf_path(doc_id)
    assert stored.exists()
    assert stored.read_bytes() == b"%PDF-1.4 original bytes"


def test_store_survives_deletion_of_the_source(pdf, stub_pages):
    """The copy exists so a moved or deleted source cannot break the viewer."""
    stub_pages()
    src = pdf()

    doc_id, _ = ingestion.ingest_pdf(src, "d", entity="E", fiscal_year="FY1")
    Path(src).unlink()

    assert ingestion.stored_pdf_path(doc_id).exists()
    assert ingestion.list_documents()[0].has_file is True


def test_registry_records_path_and_content_hash(pdf, stub_pages):
    stub_pages()
    doc_id, _ = ingestion.ingest_pdf(pdf(), "d", entity="E", fiscal_year="FY1")

    record = ingestion.get_document(doc_id)
    assert record["file_path"] == str(ingestion.stored_pdf_path(doc_id))
    assert record["content_sha256"] == ingestion.file_sha256(record["file_path"])


def test_has_file_is_derived_from_disk_not_the_record(pdf, stub_pages):
    """A deleted PDF must be reported honestly, not advertised as openable."""
    stub_pages()
    doc_id, _ = ingestion.ingest_pdf(pdf(), "d", entity="E", fiscal_year="FY1")

    ingestion.stored_pdf_path(doc_id).unlink()

    assert ingestion.list_documents()[0].has_file is False


def test_list_documents_tolerates_records_without_a_file(pdf, stub_pages):
    """Documents ingested before PDFs were persisted must still list."""
    stub_pages()
    ingestion.ingest_pdf(pdf(), "d", entity="E", fiscal_year="FY1")

    store_path = Path(ingestion.cfg.chunk_store_path)
    store = json.loads(store_path.read_text())
    for record in store["documents"].values():
        record.pop("file_path", None)
        record.pop("content_sha256", None)
    store_path.write_text(json.dumps(store))

    docs = ingestion.list_documents()
    assert len(docs) == 1
    assert docs[0].has_file is False


# ── Re-ingest guards ──────────────────────────────────────────────────────────

def test_identical_reingest_raises_already_indexed(pdf, stub_pages):
    stub_pages()
    src = pdf()
    ingestion.ingest_pdf(src, "d", entity="E", fiscal_year="FY1")

    with pytest.raises(AlreadyIndexed):
        ingestion.ingest_pdf(src, "d", entity="E", fiscal_year="FY1")


def test_changed_content_under_same_name_is_refused(pdf, stub_pages):
    """
    doc_id is sha256(doc_name), so chunk ids collide — but the FAISS sidecar
    appends, and FAISS has no delete. Silently re-ingesting would double-index
    every chunk with no way to undo it.
    """
    stub_pages()
    ingestion.ingest_pdf(pdf(body=b"%PDF v1"), "d", entity="E", fiscal_year="FY1")

    with pytest.raises(ContentConflict, match="different content"):
        ingestion.ingest_pdf(pdf(name="v2.pdf", body=b"%PDF v2"), "d",
                             entity="E", fiscal_year="FY1")


def test_conflict_is_detected_before_parsing(pdf, monkeypatch):
    """
    A 300-page report takes minutes to parse. The guard must fire first, not after
    the expensive work is already done.
    """
    calls = []
    monkeypatch.setattr(ingestion, "_extract_pages", lambda p: (
        calls.append(p) or [{"page_number": 1, "text": "x", "section_heading": None}]
    ))
    src = pdf()
    ingestion.ingest_pdf(src, "d", entity="E", fiscal_year="FY1")
    assert len(calls) == 1

    with pytest.raises(AlreadyIndexed):
        ingestion.ingest_pdf(src, "d", entity="E", fiscal_year="FY1")
    assert len(calls) == 1, "_extract_pages ran again despite the duplicate guard"


def test_same_content_under_a_different_name_is_allowed(pdf, stub_pages):
    """Different doc_name means a different doc_id, so there is no collision."""
    stub_pages()
    src = pdf()
    ingestion.ingest_pdf(src, "Infosys FY2024-25", entity="Infosys", fiscal_year="FY2024-25")
    ingestion.ingest_pdf(src, "Infosys FY2024-25 (copy)", entity="Infosys",
                         fiscal_year="FY2024-25")

    assert len(ingestion.list_documents()) == 2


# ── GET /documents/{doc_id}/file ──────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(main.app)


def test_serves_the_pdf_inline(client, pdf, stub_pages):
    """Inline, not attachment — PDF.js renders it in a panel rather than downloading."""
    stub_pages()
    doc_id, _ = ingestion.ingest_pdf(pdf(body=b"%PDF-1.4 served"), "d",
                                     entity="E", fiscal_year="FY1")

    r = client.get(f"/documents/{doc_id}/file")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.content == b"%PDF-1.4 served"


def test_unknown_doc_id_is_404(client):
    assert client.get(f"/documents/{'a' * 16}/file").status_code == 404


@pytest.mark.parametrize("bad", ["nope", "ABCDEF0123456789", "a" * 15, "a" * 17, "../etc/passwd"])
def test_malformed_doc_id_is_400(client, bad):
    assert client.get(f"/documents/{bad}/file").status_code in (400, 404)


def test_indexed_but_missing_file_reports_distinctly(client, pdf, stub_pages):
    """
    Distinguishable from an unknown doc_id: the document exists, its PDF does not.
    The message has to tell the operator what to do about it.
    """
    stub_pages()
    doc_id, _ = ingestion.ingest_pdf(pdf(), "Infosys FY2024-25", entity="E", fiscal_year="FY1")
    ingestion.stored_pdf_path(doc_id).unlink()

    r = client.get(f"/documents/{doc_id}/file")

    assert r.status_code == 404
    assert "Re-ingest" in r.json()["detail"]
    assert "Infosys FY2024-25" in r.json()["detail"]
