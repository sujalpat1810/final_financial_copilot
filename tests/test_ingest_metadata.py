"""
Provenance must survive the trip from ingest arguments onto every chunk.

These tests stub _extract_pages rather than using a real PDF: the extraction path
is covered by tests/test_basis_detection.py, and what needs guarding here is the
wiring — that operator-supplied entity/fiscal_year reach ChunkMetadata unchanged,
and that per-page basis is attached to the chunks of the right page.
"""

from __future__ import annotations

import pytest

from app import ingestion
from app.basis import CONSOLIDATED, STANDALONE


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """
    Keep ingest_pdf's writes out of the real data/ directory.

    Both paths must be redirected: ingest_pdf now also copies the PDF into
    cfg.pdf_store_dir, so patching only the chunk store leaves stray files in
    data/pdf_store/.
    """
    monkeypatch.setattr(ingestion.cfg, "chunk_store_path", str(tmp_path / "chunk_store.json"))
    monkeypatch.setattr(ingestion.cfg, "pdf_store_dir", str(tmp_path / "pdf_store"))


@pytest.fixture
def fake_pdf(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4 not really a pdf")   # ingest_pdf only checks existence
    return str(path)


def _stub_pages(monkeypatch, pages: list[str]):
    monkeypatch.setattr(
        ingestion,
        "_extract_pages",
        lambda _path: [
            {"page_number": i, "text": t, "section_heading": None}
            for i, t in enumerate(pages, start=1)
        ],
    )


def test_entity_and_fiscal_year_are_applied_verbatim(fake_pdf, monkeypatch):
    _stub_pages(monkeypatch, ["Revenue from operations was 1,62,990 crore."])

    _, chunks = ingestion.ingest_pdf(
        fake_pdf, doc_name="Infosys FY2024-25", entity="Infosys", fiscal_year="FY2024-25"
    )

    assert chunks
    for chunk in chunks:
        assert chunk.metadata.entity == "Infosys"
        assert chunk.metadata.fiscal_year == "FY2024-25"


def test_fiscal_year_is_not_inferred_from_page_text(fake_pdf, monkeypatch):
    """
    The page is full of years; none of them may become the fiscal year. This is
    the behaviour the deleted _detect_fiscal_year got wrong.
    """
    _stub_pages(monkeypatch, ["Year ended March 31, 2019. In 2011 and 2004 we also grew."])

    _, chunks = ingestion.ingest_pdf(fake_pdf, doc_name="d", entity="E", fiscal_year="FY2024-25")

    assert {c.metadata.fiscal_year for c in chunks} == {"FY2024-25"}


def test_basis_is_attached_per_page(fake_pdf, monkeypatch):
    _stub_pages(monkeypatch, [
        "Management discussion and analysis",                                    # p1 none
        "Standalone Financial Statements under Indian Accounting Standards",     # p2 standalone
        "Revenue from operations 1,36,592",                                      # p3 carried
        "Consolidated Financial Statements under Indian Accounting Standards",   # p4 consolidated
        "Revenue from operations 162,990",                                       # p5 carried
        "Notice of the 44th Annual General Meeting",                             # p6 terminated
    ])

    _, chunks = ingestion.ingest_pdf(fake_pdf, doc_name="d", entity="E", fiscal_year="FY2024-25")

    basis_by_page = {c.metadata.page_number: c.metadata.basis for c in chunks}
    assert basis_by_page == {
        1: None, 2: STANDALONE, 3: STANDALONE, 4: CONSOLIDATED, 5: CONSOLIDATED, 6: None,
    }


def test_document_registry_records_basis_page_counts(fake_pdf, monkeypatch):
    """A detection failure should be visible in /documents, not silent."""
    _stub_pages(monkeypatch, [
        "Standalone Financial Statements under Indian Accounting Standards",
        "balance sheet",
        "Consolidated Financial Statements under Indian Accounting Standards",
    ])

    ingestion.ingest_pdf(fake_pdf, doc_name="d", entity="Infosys", fiscal_year="FY2024-25")

    doc = ingestion.list_documents()[0]
    assert doc.entity == "Infosys"
    assert doc.fiscal_year == "FY2024-25"
    assert doc.standalone_pages == 2
    assert doc.consolidated_pages == 1


def test_undetected_basis_stays_none_rather_than_defaulting(fake_pdf, monkeypatch):
    """
    A report whose headings we don't recognise must yield None throughout. Failing
    closed to 'undetermined' qualifies the answer; guessing a basis misleads.
    """
    _stub_pages(monkeypatch, ["Revenue 100", "Profit 20", "Some other publisher's layout"])

    _, chunks = ingestion.ingest_pdf(fake_pdf, doc_name="d", entity="E", fiscal_year="FY1")

    assert all(c.metadata.basis is None for c in chunks)


# ── Indexed text ──────────────────────────────────────────────────────────────
# Measured on the real corpus: without a provenance line, the consolidated P&L
# page for "What was Infosys consolidated revenue in FY2024-25?" was absent from
# the vector top-200, sat at BM25 rank 118, and scored -1.22 from the reranker
# against +3.76 for a narrative page. The chunk is a reserialised table; nothing
# in its text says Infosys, FY2024-25 or consolidated.

def test_indexed_text_carries_provenance_for_retrieval():
    from app.models import Chunk, ChunkMetadata

    meta = ChunkMetadata(
        chunk_id="c", doc_id="d", doc_name="Infosys FY2024-25", page_number=276,
        entity="Infosys", fiscal_year="FY2024-25", basis="consolidated",
    )
    chunk = Chunk(chunk_id="c", text="[TABLE]\nRevenue from operations | 162,990", metadata=meta)

    indexed = chunk.indexed_text
    for term in ("Infosys", "FY2024-25", "consolidated", "276"):
        assert term in indexed, f"{term!r} must be searchable"
    assert chunk.text in indexed


def test_indexed_text_leaves_the_raw_text_untouched():
    """
    text is what the answer quotes, what the excerpt shows, and what the source
    viewer searches for in the PDF text layer. The provenance line is not printed
    on the page, so folding it into text would send the viewer hunting for a
    string that cannot be found there.
    """
    from app.models import Chunk, ChunkMetadata

    meta = ChunkMetadata(chunk_id="c", doc_id="d", doc_name="n", page_number=1,
                         entity="Infosys", fiscal_year="FY2024-25", basis="standalone")
    chunk = Chunk(chunk_id="c", text="Revenue grew.", metadata=meta)

    assert chunk.text == "Revenue grew."
    assert chunk.indexed_text != chunk.text


def test_undetermined_basis_claims_nothing():
    """
    A page outside the statement blocks could be the board's report, ESG or a
    ten-year summary. Inventing a label would misdescribe it and let it compete
    with the real statements for a query that names a basis.
    """
    from app.models import Chunk, ChunkMetadata

    meta = ChunkMetadata(chunk_id="c", doc_id="d", doc_name="n", page_number=76,
                         entity="Infosys", fiscal_year="FY2024-25", basis=None)
    line = meta.provenance_line()

    assert "financial statements" not in line
    assert "highlights" not in line
    assert "Infosys" in line and "page 76" in line


def test_documents_without_provenance_are_indexed_unchanged():
    """Anything ingested before entity/fiscal_year were required must still index."""
    from app.models import Chunk, ChunkMetadata

    meta = ChunkMetadata(chunk_id="c", doc_id="d", doc_name="n", page_number=1)
    chunk = Chunk(chunk_id="c", text="legacy chunk", metadata=meta)

    assert meta.provenance_line() == ""
    assert chunk.indexed_text == "legacy chunk"


def test_bm25_indexes_the_provenance_line(tmp_path, monkeypatch):
    """The line is only useful if the index actually tokenises it."""
    from app.models import Chunk, ChunkMetadata
    from app.retrieval import BM25Index

    def make(page, basis, text):
        meta = ChunkMetadata(chunk_id=f"c{page}", doc_id="d", doc_name="Infosys FY2024-25",
                             page_number=page, entity="Infosys", fiscal_year="FY2024-25",
                             basis=basis)
        return Chunk(chunk_id=f"c{page}", text=text, metadata=meta)

    index = BM25Index()
    index.build([
        make(276, "consolidated", "[TABLE]\nRevenue from operations | 162,990"),
        make(196, "standalone", "[TABLE]\nRevenue from operations | 1,36,592"),
    ])

    hits = index.search("consolidated financial statements", top_k=5)
    assert hits, "the provenance line was not indexed"
    assert hits[0].chunk.metadata.basis == "consolidated"
