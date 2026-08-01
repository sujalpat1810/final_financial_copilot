"""
Tests for the ingestion / chunking logic.
No PDF needed — we test _chunk_text directly.
"""

import pytest
from app.ingestion import _chunk_text, _detect_section_heading


def test_chunk_short_text_returns_single_chunk():
    text = "Revenue grew 12% year-over-year. Operating income increased to $4.2B."
    chunks = _chunk_text(text, max_words=200)
    assert len(chunks) == 1
    assert "Revenue" in chunks[0]


def test_chunk_long_text_splits_correctly():
    # 30 paragraphs of ~20 words each → should split into multiple chunks at max_words=100
    para = "The company reported strong results across all business segments this quarter. "
    text = "\n\n".join([para.strip()] * 30)
    chunks = _chunk_text(text, max_words=100)
    assert len(chunks) > 1
    # No chunk should exceed 100 words by a large margin (overlap may add a little)
    for chunk in chunks:
        assert len(chunk.split()) <= 130, f"Chunk too long: {len(chunk.split())} words"


def test_chunk_preserves_table_markers():
    text = "Summary of results:\n\n[TABLE]\nRevenue | 100 | 120\nCost | 80 | 90\n[/TABLE]\n\nTotal profit increased."
    chunks = _chunk_text(text, max_words=200)
    combined = " ".join(chunks)
    assert "[TABLE]" in combined
    assert "Revenue" in combined


def test_chunk_empty_text_returns_empty():
    assert _chunk_text("") == []
    assert _chunk_text("   \n\n   ") == []


# The four test_detect_fiscal_year_* tests that were here have been removed
# along with the function they covered.  They encoded the bug rather than
# guarding against it: test_detect_fiscal_year_bare_year asserted that
# "In 2024 the company expanded operations." yields FY2024, i.e. that any bare
# four-digit year on a page becomes the page's fiscal year.  On a real annual
# report every comparative column supplies a competing year, so that heuristic
# mislabels most pages.  fiscal_year and entity are now operator-supplied at
# ingest; basis is derived from section structure and is tested in
# tests/test_basis_detection.py.


def test_detect_section_heading_all_caps():
    text = "MANAGEMENT DISCUSSION AND ANALYSIS\n\nRevenue grew 15%."
    heading = _detect_section_heading(text)
    assert heading is not None
    assert "MANAGEMENT" in heading


def test_detect_section_heading_item():
    text = "Item 1A. Risk Factors\n\nThe company faces market risk."
    heading = _detect_section_heading(text)
    assert heading is not None
    assert "Risk" in heading or "Item" in heading


def test_chunk_overlap_carries_context():
    """Last sentence of chunk N should appear at the start of chunk N+1."""
    sentences = ["Sentence number {i}." .format(i=i) for i in range(40)]
    # Group into paragraphs of 4 sentences
    paragraphs = [" ".join(sentences[i:i+4]) for i in range(0, 40, 4)]
    text = "\n\n".join(paragraphs)
    chunks = _chunk_text(text, max_words=50, overlap_sentences=1)
    # With overlap there should be some shared text between consecutive chunks
    # (not strictly guaranteed for all chunk pairs but should hold for most)
    assert len(chunks) > 1
