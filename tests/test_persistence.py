"""
The index must survive a server restart.

The brief said to verify this rather than assume it, because the demo depends on
it: the corpus is ingested once via scripts/ingest.py, and a restart that lost
the index would mean re-embedding six 300-page reports before anyone could ask a
question.

Three things have to persist, and they persist differently:

  * chunk text + metadata -> data/chunk_store.json, written at ingest
  * vectors                -> the FAISS index + its metadata sidecar, on disk
  * BM25                   -> NOT persisted as an index. It is rebuilt in memory
                              at startup from the chunk store (app/main.lifespan),
                              because BM25Plus has no serialisable form here.

The third is the one worth testing: it is the only one that depends on a startup
step rather than on a file existing.
"""

from __future__ import annotations

import pytest

from app import ingestion
from app.basis import CONSOLIDATED, STANDALONE
from app.retrieval import BM25Index


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion.cfg, "chunk_store_path", str(tmp_path / "chunk_store.json"))
    monkeypatch.setattr(ingestion.cfg, "pdf_store_dir", str(tmp_path / "pdf_store"))


@pytest.fixture
def ingested(tmp_path, monkeypatch):
    """Ingest two documents, as scripts.ingest would."""
    monkeypatch.setattr(ingestion, "_extract_pages", lambda path: [
        {"page_number": 1, "text": "Standalone Financial Statements under Indian "
                                   "Accounting Standards", "section_heading": None},
        {"page_number": 2, "text": "Revenue from operations 1,36,592", "section_heading": None},
        {"page_number": 3, "text": "Consolidated Financial Statements under Indian "
                                   "Accounting Standards", "section_heading": None},
        {"page_number": 4, "text": "Revenue from operations 162,990", "section_heading": None},
    ])
    made = []
    for name, entity, fy in [("Infosys FY2024-25", "Infosys", "FY2024-25"),
                             ("TCS FY2024-25", "TCS", "FY2024-25")]:
        pdf = tmp_path / f"{name}.pdf"
        pdf.write_bytes(b"%PDF-1.4 " + name.encode())
        made.append(ingestion.ingest_pdf(str(pdf), name, entity=entity, fiscal_year=fy))
    return made


def test_chunks_survive_a_restart(ingested):
    """
    get_all_chunks reads from disk, so a fresh process sees everything. This is
    what lifespan feeds to the BM25 rebuild.
    """
    total = sum(len(chunks) for _, chunks in ingested)
    recovered = ingestion.get_all_chunks()
    assert len(recovered) == total
    assert {c.chunk_id for c in recovered} == {
        c.chunk_id for _, chunks in ingested for c in chunks
    }


def test_provenance_survives_a_restart(ingested):
    """
    Entity, fiscal year and basis are what qualify every figure. If they did not
    round-trip through the store, answers after a restart would silently lose
    their attribution — worse than losing the index outright, because it fails
    quietly.
    """
    by_id = {c.chunk_id: c for c in ingestion.get_all_chunks()}
    for _, chunks in ingested:
        for original in chunks:
            recovered = by_id[original.chunk_id].metadata
            assert recovered.entity == original.metadata.entity
            assert recovered.fiscal_year == original.metadata.fiscal_year
            assert recovered.basis == original.metadata.basis
            assert recovered.doc_id == original.metadata.doc_id
            assert recovered.page_number == original.metadata.page_number

    bases = {c.metadata.basis for c in by_id.values()}
    assert STANDALONE in bases and CONSOLIDATED in bases


def test_bm25_rebuilds_from_the_persisted_store(ingested):
    """
    The actual restart path. BM25 is not serialised — lifespan rebuilds it from
    get_all_chunks(), so this reproduces that startup step against a fresh index
    and confirms it can retrieve.
    """
    fresh = BM25Index()
    assert fresh.search("revenue", top_k=5) == [], "a new index starts empty"

    # This is what app.main.lifespan does on boot.
    chunks = ingestion.get_all_chunks()
    fresh.build(chunks)

    results = fresh.search("revenue operations", top_k=10)
    assert results, "BM25 found nothing after rebuilding from the chunk store"
    assert all(r.chunk.metadata.doc_id for r in results)


def test_rebuilt_bm25_can_still_filter_by_document(ingested):
    """Document filtering depends on metadata that came back off disk."""
    index = BM25Index()
    index.build(ingestion.get_all_chunks())

    results = index.search("revenue", top_k=10, filter_doc_name="TCS FY2024-25")
    assert results
    assert {r.chunk.metadata.doc_name for r in results} == {"TCS FY2024-25"}


def test_stored_pdfs_survive_a_restart(ingested):
    """Citations must still open their source page after a restart."""
    for doc in ingestion.list_documents():
        assert doc.has_file, f"{doc.doc_name} lost its PDF"
        assert ingestion.stored_pdf_path(doc.doc_id).exists()


def test_document_registry_survives_a_restart(ingested):
    docs = {d.doc_name: d for d in ingestion.list_documents()}
    assert set(docs) == {"Infosys FY2024-25", "TCS FY2024-25"}
    assert docs["Infosys FY2024-25"].entity == "Infosys"
    assert docs["TCS FY2024-25"].entity == "TCS"
    # Basis page counts are what the sidebar shows; they come from the registry.
    assert docs["Infosys FY2024-25"].standalone_pages == 2
    assert docs["Infosys FY2024-25"].consolidated_pages == 2


def test_reingesting_after_a_restart_is_skipped_not_duplicated(ingested, tmp_path):
    """
    The practical consequence: re-running scripts.ingest after a restart must be
    a no-op, not a second copy of the corpus. FAISS has no delete, so a silent
    duplicate could not be undone.
    """
    pdf = tmp_path / "Infosys FY2024-25.pdf"
    with pytest.raises(ingestion.AlreadyIndexed):
        ingestion.ingest_pdf(str(pdf), "Infosys FY2024-25",
                             entity="Infosys", fiscal_year="FY2024-25")
