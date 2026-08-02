"""
The frontend and the response models must agree on field names.

This is the drift that fails silently: rename `is_table` to `table` in
models.py and nothing errors — the frontend just reads `undefined`, every table
chunk loses its flag, and the viewer starts trying to highlight reserialised
pipe-delimited text against a PDF page. No test fails, no exception is raised,
the demo just quietly gets worse.

The frontend uses camelCase for its own identifiers and snake_case only for API
fields, so every snake_case property access in frontend/js is a field this API
must provide. That convention is what makes this checkable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models import (
    DocumentInfo,
    DocumentListResponse,
    HealthResponse,
    IngestResponse,
    QueryResponse,
    SourceCitation,
)

JS_DIR = Path(__file__).resolve().parent.parent / "frontend" / "js"

# Every field the API can put on the wire.
API_FIELDS: set[str] = set()
for model in (QueryResponse, SourceCitation, DocumentInfo, DocumentListResponse,
              HealthResponse, IngestResponse):
    API_FIELDS |= set(model.model_fields)

# snake_case property accesses that are not API fields.
NOT_API = {
    # DOM and browser APIs
    "content_type", "last_modified",
}

_ACCESS = re.compile(r"\.([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")


def _js_sources() -> list[Path]:
    return sorted(p for p in JS_DIR.glob("*.js") if not p.name.endswith(".test.js"))


def test_there_are_js_sources_to_check():
    assert _js_sources(), "no frontend modules found"


@pytest.mark.parametrize("path", _js_sources(), ids=lambda p: p.name)
def test_every_snake_case_field_the_frontend_reads_exists(path: Path):
    source = path.read_text(encoding="utf-8")
    # Strip comments so documentation mentioning an old field name cannot fail
    # the test, and string literals so form-field names are not treated as reads.
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//.*", "", source)

    used = {name for name in _ACCESS.findall(source)} - NOT_API
    unknown = sorted(used - API_FIELDS)
    assert not unknown, (
        f"{path.name} reads field(s) the API does not define: {unknown}\n"
        f"Either the model lost a field or the frontend has a typo."
    )


def test_query_response_carries_everything_the_finding_card_needs():
    """Named explicitly, so removing one is a deliberate act with a failing test."""
    required = {
        "question", "answer", "sources", "answer_source",
        "confidence", "confidence_reason",
        "abstained", "abstention_reason",
        "documents_searched", "chunks_searched",
        "retrieval_latency_ms", "generation_latency_ms", "total_latency_ms",
    }
    assert required <= set(QueryResponse.model_fields)


def test_source_citation_carries_everything_a_chip_needs():
    """
    doc_id is the one that matters most: without it a citation cannot request
    the PDF, so it could never open its source page.
    """
    required = {
        "doc_id", "chunk_id", "doc_name", "page_number",
        "entity", "fiscal_year", "basis",
        "relevance", "rerank_score", "is_table", "excerpt", "section_title",
    }
    assert required <= set(SourceCitation.model_fields)


def test_document_info_carries_what_the_sidebar_shows():
    required = {
        "doc_id", "doc_name", "entity", "fiscal_year",
        "pages", "chunks", "standalone_pages", "consolidated_pages", "has_file",
    }
    assert required <= set(DocumentInfo.model_fields)


def test_basis_is_nullable_so_undetermined_can_be_expressed():
    """
    If basis were non-optional it would need a default, and any default is a
    silent guess about which set of financial statements a figure came from.
    """
    field = SourceCitation.model_fields["basis"]
    assert field.default is None
    assert not field.is_required()
