"""
Tests for the hybrid retrieval merge and BM25 logic.
Uses dummy chunks — no real embeddings or vector store needed.
"""

import pytest
from app.models import Chunk, ChunkMetadata, RetrievedChunk
from app.retrieval import BM25Index, _tokenise


def _make_chunk(chunk_id: str, text: str, doc_name: str = "Test Corp 10-K",
                page: int = 1, fiscal_year: str = "FY2023") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        metadata=ChunkMetadata(
            chunk_id=chunk_id,
            doc_id="test_doc",
            doc_name=doc_name,
            page_number=page,
            fiscal_year=fiscal_year,
        ),
    )


# ── Tokeniser tests ───────────────────────────────────────────────────────────

def test_tokenise_lowercases():
    tokens = _tokenise("Revenue EBITDA 2023")
    assert "revenue" in tokens
    assert "ebitda" in tokens
    assert "2023" in tokens


def test_tokenise_strips_punctuation():
    tokens = _tokenise("net income: $4.2B (up 12%).")
    assert "net" in tokens
    assert "income" in tokens
    assert "4" in tokens or "4.2b" in tokens or "4" in tokens


# ── BM25 index tests ──────────────────────────────────────────────────────────

def test_bm25_build_and_search():
    chunks = [
        _make_chunk("c1", "Revenue for fiscal year 2023 was ten billion dollars."),
        _make_chunk("c2", "Operating expenses increased due to supply chain disruption."),
        _make_chunk("c3", "Net income margin improved by two percentage points."),
    ]
    bm25 = BM25Index()
    bm25.build(chunks)

    results = bm25.search("revenue fiscal year", top_k=3)
    assert len(results) > 0
    # The chunk about revenue should rank first
    assert results[0].chunk.chunk_id == "c1"


def test_bm25_returns_empty_on_empty_index():
    bm25 = BM25Index()
    results = bm25.search("revenue", top_k=5)
    assert results == []


def test_bm25_filter_by_doc_name():
    # Give each chunk a DISTINCT vocabulary so "revenue earnings" doesn't appear
    # in both docs — otherwise BM25 IDF collapses to 0 for shared terms.
    chunks = [
        _make_chunk("c1", "Revenue and earnings growth exceeded expectations this quarter.", doc_name="Alpha Corp"),
        _make_chunk("c2", "Cost reduction initiatives and expense management programme launched.", doc_name="Beta Corp"),
    ]
    bm25 = BM25Index()
    bm25.build(chunks)

    results = bm25.search("revenue earnings", top_k=5, filter_doc_name="Alpha Corp")
    assert all(r.chunk.metadata.doc_name == "Alpha Corp" for r in results)
    assert len(results) == 1


def test_bm25_filter_by_fiscal_year():
    chunks = [
        _make_chunk("c1", "Revenue in FY2022 was eight billion.", fiscal_year="FY2022"),
        _make_chunk("c2", "Revenue in FY2023 was ten billion.", fiscal_year="FY2023"),
    ]
    bm25 = BM25Index()
    bm25.build(chunks)

    results = bm25.search("revenue", top_k=5, filter_fiscal_year="FY2023")
    assert all(r.chunk.metadata.fiscal_year == "FY2023" for r in results)


def test_bm25_add_chunks_incremental():
    bm25 = BM25Index()
    c1 = _make_chunk("c1", "Initial revenue data for 2022.")
    bm25.build([c1])

    c2 = _make_chunk("c2", "New operating income data for 2023.")
    bm25.add_chunks([c2])

    results = bm25.search("operating income", top_k=5)
    ids = [r.chunk.chunk_id for r in results]
    assert "c2" in ids


def test_bm25_zero_score_results_excluded():
    chunks = [_make_chunk("c1", "Apple orange banana fruit salad.")]
    bm25 = BM25Index()
    bm25.build(chunks)

    # Query completely unrelated to the chunk
    results = bm25.search("revenue EBITDA fiscal year", top_k=5)
    # All scores should be 0 or near 0 — results with score <= 0 are excluded
    assert all(r.bm25_score > 0 for r in results)


# ── Merge / dedup logic (tested inline, not through HybridRetriever) ──────────

def test_merge_dedup():
    """Verify that chunk_ids found by both retrievers are merged, not duplicated."""
    shared_chunk = _make_chunk("shared", "This chunk appears in both results.")

    vector_results = [
        RetrievedChunk(chunk=shared_chunk, vector_score=0.9, retrieval_source="vector"),
        RetrievedChunk(chunk=_make_chunk("v_only", "Vector only result."),
                       vector_score=0.7, retrieval_source="vector"),
    ]
    bm25_results = [
        RetrievedChunk(chunk=shared_chunk, bm25_score=5.2, retrieval_source="bm25"),
        RetrievedChunk(chunk=_make_chunk("b_only", "BM25 only result."),
                       bm25_score=4.1, retrieval_source="bm25"),
    ]

    # Replicate the merge logic from HybridRetriever.retrieve
    merged: dict[str, RetrievedChunk] = {}
    for r in vector_results:
        merged[r.chunk.chunk_id] = r
    for r in bm25_results:
        cid = r.chunk.chunk_id
        if cid in merged:
            merged[cid].bm25_score = r.bm25_score
            merged[cid].retrieval_source = "hybrid"
        else:
            merged[cid] = r

    assert len(merged) == 3   # shared, v_only, b_only
    assert merged["shared"].retrieval_source == "hybrid"
    assert merged["shared"].vector_score == 0.9
    assert merged["shared"].bm25_score == 5.2
    assert merged["v_only"].retrieval_source == "vector"
    assert merged["b_only"].retrieval_source == "bm25"
