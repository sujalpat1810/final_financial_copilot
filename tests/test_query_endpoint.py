"""
/query behaviour around the abstention gate.

The retriever and vector store are stubbed, so these run without torch, faiss or
an API key.  What is under test is the endpoint's contract — that abstention skips
generation entirely, that structured citation data is present, and that the
response carries what the abstention card needs to render.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import cfg
from app.models import Chunk, ChunkMetadata, RetrievedChunk


@pytest.fixture(autouse=True)
def pinned_thresholds(monkeypatch):
    monkeypatch.setattr(cfg, "rerank_high_threshold", 2.0)
    monkeypatch.setattr(cfg, "rerank_moderate_threshold", -2.0)
    monkeypatch.setattr(cfg, "rerank_abstain_threshold", -6.0)
    monkeypatch.setattr(cfg, "confidence_min_supporting", 2)


def _chunk(page: int, text: str, basis: str | None = "consolidated") -> Chunk:
    chunk_id = f"doc123_p{page}_0"
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        metadata=ChunkMetadata(
            chunk_id=chunk_id,
            doc_id="doc123",
            doc_name="Infosys FY2024-25",
            page_number=page,
            entity="Infosys",
            fiscal_year="FY2024-25",
            basis=basis,
        ),
    )


class _StubRetriever:
    def __init__(self, results):
        self._results = results

    def retrieve(self, **_kwargs):
        return self._results


class _StubStore:
    def get_chunk_count(self):
        return 5448


@dataclass
class _StubDoc:
    """
    Stands in for DocumentInfo.

    Carries `entity` because /query reads the indexed entity set to run the
    unindexed-company gate (app/entities.py).  A bare object() was enough when
    the endpoint only counted documents; it silently became a lie the moment the
    count stopped being all the endpoint needed.
    """
    entity: str


# Mirrors the demo corpus: two companies across three documents.
_STUB_DOCS = [_StubDoc("Infosys"), _StubDoc("Infosys"), _StubDoc("TCS")]


@pytest.fixture
def client(monkeypatch):
    """A TestClient that bypasses lifespan (which would load real models)."""
    monkeypatch.setattr(main, "list_documents", lambda: list(_STUB_DOCS))
    monkeypatch.setitem(main._state, "vs", _StubStore())
    return TestClient(main.app)


def _install(monkeypatch, results):
    monkeypatch.setitem(main._state, "retriever", _StubRetriever(results))


def _generation_spy(monkeypatch):
    calls = []

    def spy(question, chunks):
        calls.append(question)
        return "generated answer [Page 276]", "generated"

    monkeypatch.setattr(main, "generate_answer", spy)
    return calls


# ── Abstention ────────────────────────────────────────────────────────────────

def test_below_floor_abstains_without_generating(client, monkeypatch):
    """Generation must be skipped, not run and discarded."""
    calls = _generation_spy(monkeypatch)
    _install(monkeypatch, [
        RetrievedChunk(chunk=_chunk(43, "competitive landscape"), rerank_score=-7.8),
        RetrievedChunk(chunk=_chunk(28, "industry overview"), rerank_score=-8.2),
    ])

    body = client.post("/query", json={"question": "What was Wipro's revenue in FY2025?"}).json()

    assert calls == [], "generation was called despite abstaining"
    assert body["abstained"] is True
    assert body["confidence"] == "insufficient"
    assert body["answer"] == ""
    assert body["abstention_reason"]
    assert body["generation_latency_ms"] == 0.0


def test_unindexed_company_abstains_even_on_strong_scores(client, monkeypatch):
    """
    The gap the entity gate closes, at the endpoint level.

    These scores are the profile of a confident answer — before the gate this
    question returned "moderate" confidence and a generated answer built from
    Infosys and TCS passages.  Retrieval is not at fault: a peer's revenue
    question genuinely matches a revenue table.  Only the company is wrong, and
    no score can fix that.
    """
    calls = _generation_spy(monkeypatch)
    _install(monkeypatch, [
        RetrievedChunk(chunk=_chunk(30, "revenue from operations"), rerank_score=7.5),
        RetrievedChunk(chunk=_chunk(31, "segment revenue"), rerank_score=6.9),
    ])

    body = client.post(
        "/query", json={"question": "What was Wipro's revenue in FY2025?"}
    ).json()

    assert calls == [], "generation ran for a company that is not indexed"
    assert body["abstained"] is True
    assert body["confidence"] == "insufficient"
    assert body["answer"] == ""
    assert "Wipro" in body["abstention_reason"]
    # The refusal must say what IS covered, or the reader has no next step.
    assert "Infosys" in body["abstention_reason"]
    assert "TCS" in body["abstention_reason"]
    # Near-misses still render, so the card can show the search really ran.
    assert body["sources"]


def test_indexed_company_with_strong_scores_still_generates(client, monkeypatch):
    """The gate must not have made the happy path abstain."""
    calls = _generation_spy(monkeypatch)
    _install(monkeypatch, [
        RetrievedChunk(chunk=_chunk(30, "revenue from operations"), rerank_score=7.5),
        RetrievedChunk(chunk=_chunk(31, "segment revenue"), rerank_score=6.9),
    ])

    body = client.post(
        "/query", json={"question": "What was Infosys consolidated revenue in FY2024-25?"}
    ).json()

    assert body["abstained"] is False
    assert body["confidence"] == "high"
    assert len(calls) == 1


def test_abstention_reports_what_was_searched(client, monkeypatch):
    """A non-answer that states its own scope is useful; a bare 'not found' isn't."""
    _generation_spy(monkeypatch)
    _install(monkeypatch, [RetrievedChunk(chunk=_chunk(43, "x"), rerank_score=-9.0)])

    body = client.post("/query", json={"question": "Wipro revenue?"}).json()

    assert body["documents_searched"] == 3
    assert body["chunks_searched"] == 5448


def test_abstention_still_returns_near_misses(client, monkeypatch):
    """The card shows the closest matches and their relevance."""
    _generation_spy(monkeypatch)
    _install(monkeypatch, [
        RetrievedChunk(chunk=_chunk(43, "closest"), rerank_score=-7.8),
        RetrievedChunk(chunk=_chunk(28, "next"), rerank_score=-8.4),
    ])

    sources = client.post("/query", json={"question": "Wipro revenue?"}).json()["sources"]

    assert [s["page_number"] for s in sources] == [43, 28]
    assert [s["relevance"] for s in sources] == [11, 8]


def test_empty_retrieval_abstains(client, monkeypatch):
    calls = _generation_spy(monkeypatch)
    _install(monkeypatch, [])

    body = client.post("/query", json={"question": "anything at all"}).json()

    assert calls == []
    assert body["abstained"] is True
    assert body["sources"] == []


# ── Normal answers ────────────────────────────────────────────────────────────

def test_strong_evidence_generates_with_high_confidence(client, monkeypatch):
    calls = _generation_spy(monkeypatch)
    _install(monkeypatch, [
        RetrievedChunk(chunk=_chunk(276, "[TABLE]\nRevenue | 162,990"), rerank_score=6.4),
        RetrievedChunk(chunk=_chunk(196, "standalone revenue"), rerank_score=3.1),
        RetrievedChunk(chunk=_chunk(181, "index"), rerank_score=2.2),
    ])

    body = client.post("/query", json={"question": "Infosys consolidated revenue FY2024-25?"}).json()

    assert len(calls) == 1
    assert body["abstained"] is False
    assert body["confidence"] == "high"
    assert body["confidence_reason"]
    assert body["answer"] == "generated answer [Page 276]"


def test_citations_carry_provenance_and_addressability(client, monkeypatch):
    """
    Without doc_id the frontend cannot request the PDF, so a citation could never
    open its source page. Provenance travels with the page number, not separately.
    """
    _generation_spy(monkeypatch)
    _install(monkeypatch, [
        RetrievedChunk(chunk=_chunk(276, "[TABLE]\nRevenue | 162,990"), rerank_score=6.4),
        RetrievedChunk(chunk=_chunk(276, "plain prose", basis=None), rerank_score=2.5),
    ])

    sources = client.post("/query", json={"question": "revenue?"}).json()["sources"]

    assert sources[0]["doc_id"] == "doc123"
    assert sources[0]["chunk_id"] == "doc123_p276_0"
    assert sources[0]["entity"] == "Infosys"
    assert sources[0]["fiscal_year"] == "FY2024-25"
    assert sources[0]["basis"] == "consolidated"
    assert sources[0]["rerank_score"] == 6.4
    assert sources[0]["relevance"] == 82


def test_table_chunks_are_flagged_for_the_viewer(client, monkeypatch):
    """
    Reserialised table text will never match the PDF text layer, so the viewer
    must know to skip highlight matching rather than show a wrong highlight.
    """
    _generation_spy(monkeypatch)
    _install(monkeypatch, [
        RetrievedChunk(chunk=_chunk(276, "[TABLE]\nRevenue | 162,990\n[/TABLE]"), rerank_score=6.4),
        RetrievedChunk(chunk=_chunk(98, "narrative prose about revenue"), rerank_score=3.0),
    ])

    sources = client.post("/query", json={"question": "revenue?"}).json()["sources"]

    assert sources[0]["is_table"] is True
    assert sources[1]["is_table"] is False


def test_undetermined_basis_is_returned_as_null_not_guessed(client, monkeypatch):
    """The UI must be able to show 'not determined'; it must never be filled in."""
    _generation_spy(monkeypatch)
    _install(monkeypatch, [
        RetrievedChunk(chunk=_chunk(69, "highlights table", basis=None), rerank_score=4.0),
        RetrievedChunk(chunk=_chunk(70, "more highlights", basis=None), rerank_score=2.1),
    ])

    sources = client.post("/query", json={"question": "revenue?"}).json()["sources"]

    assert sources[0]["basis"] is None


def test_weak_but_answerable_evidence_still_generates(client, monkeypatch):
    """LOW is an answer with a caution, not an abstention."""
    calls = _generation_spy(monkeypatch)
    _install(monkeypatch, [RetrievedChunk(chunk=_chunk(50, "vague"), rerank_score=-4.5)])

    body = client.post("/query", json={"question": "something vague?"}).json()

    assert len(calls) == 1
    assert body["confidence"] == "low"
    assert body["abstained"] is False
