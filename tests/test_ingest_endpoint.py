"""
/ingest contract, and the CLI ingest script's manifest handling.

The endpoint now hands ingest_pdf to run_in_threadpool with POSITIONAL arguments,
so a change to that signature would silently bind entity to doc_name rather than
failing. test_entity_and_fiscal_year_reach_the_pipeline is what catches it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ingestion, main
from scripts.ingest import _fmt, _load_manifest


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion.cfg, "chunk_store_path", str(tmp_path / "chunk_store.json"))
    monkeypatch.setattr(ingestion.cfg, "pdf_store_dir", str(tmp_path / "pdf_store"))


class _StubEmbed:
    def embed_documents(self, texts):
        return [[0.1] * 384 for _ in texts]


class _StubIndex:
    def __init__(self):
        self.added = []

    def add_chunks(self, chunks, embeddings=None):
        self.added.extend(chunks)

    def get_chunk_count(self):
        return len(self.added)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ingestion,
        "_extract_pages",
        lambda _p: [{"page_number": 1, "text": "Revenue from operations 1,62,990",
                     "section_heading": None}],
    )
    monkeypatch.setitem(main._state, "embed", _StubEmbed())
    monkeypatch.setitem(main._state, "vs", _StubIndex())
    monkeypatch.setitem(main._state, "bm25", _StubIndex())
    # /ingest writes its upload under data/; keep that out of the repo.
    monkeypatch.chdir(tmp_path)
    return TestClient(main.app)


def _upload(client, **form):
    payload = {"entity": "Infosys", "fiscal_year": "FY2024-25", **form}
    return client.post(
        "/ingest",
        files={"file": ("report.pdf", b"%PDF-1.4 body", "application/pdf")},
        data=payload,
    )


# ── Endpoint ──────────────────────────────────────────────────────────────────

def test_entity_and_fiscal_year_reach_the_pipeline(client):
    """
    Guards the positional run_in_threadpool call. If ingest_pdf's parameter order
    changes, entity lands in doc_name and this fails instead of silently
    mislabelling every chunk.
    """
    r = _upload(client, doc_name="Infosys FY2024-25")
    assert r.status_code == 200, r.text

    doc = ingestion.list_documents()[0]
    assert doc.doc_name == "Infosys FY2024-25"
    assert doc.entity == "Infosys"
    assert doc.fiscal_year == "FY2024-25"


def test_entity_is_required(client):
    r = client.post(
        "/ingest",
        files={"file": ("r.pdf", b"%PDF", "application/pdf")},
        data={"fiscal_year": "FY2024-25"},
    )
    assert r.status_code == 422


@pytest.mark.parametrize("form", [
    {"entity": "   ", "fiscal_year": "FY2024-25"},
    {"entity": "Infosys", "fiscal_year": "  "},
])
def test_blank_provenance_is_rejected(client, form):
    r = client.post("/ingest", files={"file": ("r.pdf", b"%PDF", "application/pdf")}, data=form)
    assert r.status_code == 400


def test_non_pdf_is_rejected(client):
    r = client.post(
        "/ingest",
        files={"file": ("notes.txt", b"text", "text/plain")},
        data={"entity": "E", "fiscal_year": "FY1"},
    )
    assert r.status_code == 400


def test_duplicate_upload_is_409_not_500(client):
    assert _upload(client, doc_name="d").status_code == 200
    r = _upload(client, doc_name="d")
    assert r.status_code == 409
    assert "already indexed" in r.json()["detail"]


def test_upload_is_indexed_in_both_stores(client):
    _upload(client, doc_name="d")
    assert main._state["vs"].get_chunk_count() > 0
    assert main._state["bm25"].get_chunk_count() > 0


# ── CLI manifest ──────────────────────────────────────────────────────────────

def test_manifest_requires_provenance(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([{"file": "a.pdf", "entity": "Infosys"}]))
    with pytest.raises(SystemExit, match="fiscal_year"):
        _load_manifest(path)


def test_manifest_rejects_non_list(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps({"file": "a.pdf"}))
    with pytest.raises(SystemExit, match="JSON list"):
        _load_manifest(path)


def test_manifest_reports_bad_json_clearly(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text("[{,}]")
    with pytest.raises(SystemExit, match="not valid JSON"):
        _load_manifest(path)


def test_missing_manifest_explains_the_format(tmp_path):
    with pytest.raises(SystemExit, match="fiscal_year"):
        _load_manifest(tmp_path / "nope.json")


def test_shipped_corpus_manifest_is_valid():
    """The committed manifest must not drift out of the schema it documents."""
    entries = _load_manifest(Path("scripts/corpus.json"))
    assert entries
    assert all(e["entity"] and e["fiscal_year"] and e["file"] for e in entries)


# ── Indian grouping in CLI output ─────────────────────────────────────────────

@pytest.mark.parametrize("n,expected", [
    (0, "0"), (999, "999"), (1000, "1,000"), (100000, "1,00,000"),
    (225712, "2,25,712"), (10000000, "1,00,00,000"), (-225712, "-2,25,712"),
])
def test_cli_uses_indian_grouping(n, expected):
    assert _fmt(n) == expected
