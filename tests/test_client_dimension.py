"""
Tests for the CA-practice domain dimensions: client, doc_type, act_version.

Three properties are load-bearing:
1. doc_id derives from client + doc_name, so two clients can each have a
   document with the same name without a ContentConflict.
2. provenance_line() carries the client, which is what makes retrieval
   client-aware through indexed_text (see Chunk.indexed_text for the measured
   ranking behaviour that mechanism exists to protect).
3. Pre-CA documents (no client, no doc_type) keep byte-identical provenance
   lines — their measured ranking behaviour must not shift under this refactor.
"""

import hashlib

from app.models import Chunk, ChunkMetadata


def _meta(**overrides) -> ChunkMetadata:
    base = dict(
        chunk_id="c1",
        doc_id="d1",
        doc_name="doc",
        page_number=3,
    )
    base.update(overrides)
    return ChunkMetadata(**base)


# ── provenance_line ───────────────────────────────────────────────────────────

def test_provenance_line_unchanged_for_legacy_documents():
    """No client/doc_type → the exact pre-CA string, ranking behaviour intact."""
    m = _meta(entity="Infosys", fiscal_year="FY2024-25", basis="consolidated")
    assert m.provenance_line() == (
        "Infosys FY2024-25 annual report, consolidated financial statements, page 3."
    )


def test_provenance_line_carries_client_and_doc_type():
    m = _meta(client="Mehta Textiles Pvt Ltd", entity="Mehta Textiles Pvt Ltd",
              fiscal_year="FY2025-26", doc_type="invoice")
    line = m.provenance_line()
    assert line.startswith("Mehta Textiles Pvt Ltd FY2025-26 invoice")
    # client == entity must not be repeated — it would be noise in the exact
    # text the embedder and reranker score.
    assert line.count("Mehta Textiles Pvt Ltd") == 1


def test_provenance_line_statute_with_act_version():
    m = _meta(entity="Income-tax Act", fiscal_year="FY2025-26",
              doc_type="statute", act_version="2025")
    line = m.provenance_line()
    assert "statute extract" in line
    assert "2025 Act numbering" in line


def test_provenance_line_client_flows_into_indexed_text():
    m = _meta(client="Sharma Electronics Pvt Ltd", fiscal_year="FY2025-26",
              doc_type="notice")
    chunk = Chunk(chunk_id="c1", text="Discrepancy in ITC claimed.", metadata=m)
    assert chunk.indexed_text.startswith("Sharma Electronics Pvt Ltd")
    # text itself stays clean — the viewer searches the PDF for it.
    assert chunk.text == "Discrepancy in ITC claimed."


# ── doc_id derivation ─────────────────────────────────────────────────────────

def test_doc_id_distinct_for_same_name_under_different_clients():
    """Mirrors the derivation in ingestion.ingest_pdf."""
    a = hashlib.sha256("Mehta Textiles Pvt Ltd/Balance Sheet".encode()).hexdigest()[:16]
    b = hashlib.sha256("Sharma Electronics Pvt Ltd/Balance Sheet".encode()).hexdigest()[:16]
    assert a != b


def test_ingest_pdf_uses_client_scoped_doc_id(tmp_path, monkeypatch):
    """End-to-end through ingest_pdf with extraction stubbed (wiring, not parsing)."""
    from app import ingestion
    from app.config import cfg

    monkeypatch.setattr(cfg, "chunk_store_path", str(tmp_path / "store.json"))
    monkeypatch.setattr(cfg, "pdf_store_dir", str(tmp_path / "pdfs"))
    monkeypatch.setattr(
        ingestion, "_extract_pages",
        lambda path: [{"page_number": 1, "text": "Taxable value 1,00,000.",
                       "section_heading": None}],
    )

    pdf = tmp_path / "upload.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")

    id_a, chunks_a = ingestion.ingest_pdf(
        str(pdf), "Balance Sheet", entity="Mehta Textiles Pvt Ltd",
        fiscal_year="FY2025-26", client="Mehta Textiles Pvt Ltd",
        doc_type="financials",
    )
    # Same doc_name, different client, different bytes must NOT conflict.
    pdf_b = tmp_path / "upload_b.pdf"
    pdf_b.write_bytes(b"%PDF-1.4 other")
    id_b, chunks_b = ingestion.ingest_pdf(
        str(pdf_b), "Balance Sheet", entity="Sharma Electronics Pvt Ltd",
        fiscal_year="FY2025-26", client="Sharma Electronics Pvt Ltd",
        doc_type="financials",
    )

    assert id_a != id_b
    meta = chunks_a[0].metadata
    assert meta.client == "Mehta Textiles Pvt Ltd"
    assert meta.doc_type == "financials"
    # The registry record carries the new fields for DocumentInfo.
    docs = {d.doc_id: d for d in ingestion.list_documents()}
    assert docs[id_a].client == "Mehta Textiles Pvt Ltd"
    assert docs[id_a].doc_type == "financials"
