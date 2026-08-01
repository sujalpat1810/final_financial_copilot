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
