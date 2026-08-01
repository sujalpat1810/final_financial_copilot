"""
Pydantic schemas for API requests and responses.
Using LangChain-style naming conventions for chunk metadata so these objects
can be handed directly to LangChain Document wrappers if needed.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


# ── Chunk / retrieval primitives ──────────────────────────────────────────────

class ChunkMetadata(BaseModel):
    chunk_id: str
    doc_id: str
    doc_name: str
    page_number: int
    section_title: str | None = None
    chunk_index: int = 0             # position of chunk within the page

    # ── Provenance ────────────────────────────────────────────────────────────
    # entity and fiscal_year are supplied by the operator at ingest, never
    # detected.  Detection was tried and removed: the first four-digit year on a
    # page is meaningless in a report full of comparative columns, and taking the
    # most common year across 300 pages is a lottery.
    entity: str | None = None        # e.g. "Infosys"
    fiscal_year: str | None = None   # e.g. "FY2024-25"

    # Which set of financial statements this chunk's page belongs to.  None means
    # undetermined — see app/basis.py.  Undetermined qualifies the answer
    # downstream; it is never silently resolved to one basis or the other.
    basis: str | None = None         # "standalone" | "consolidated" | None


class Chunk(BaseModel):
    chunk_id: str
    text: str
    metadata: ChunkMetadata


class RetrievedChunk(BaseModel):
    chunk: Chunk
    vector_score: float | None = None   # cosine similarity from FAISS/Chroma
    bm25_score: float | None = None     # BM25 score
    rerank_score: float | None = None   # cross-encoder score (higher = more relevant)
    retrieval_source: str = "unknown"   # "vector", "bm25", or "hybrid"


# ── Ingest ────────────────────────────────────────────────────────────────────

class IngestResponse(BaseModel):
    doc_id: str
    doc_name: str
    pages_processed: int
    chunks_created: int
    message: str


# ── Query ─────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3)
    fiscal_year: str | None = Field(None, description="e.g. FY2023")
    doc_name: str | None = Field(None, description="filter to a specific document")
    section_type: str | None = Field(None, description="e.g. 'balance sheet', 'risk factors'")
    top_n: int | None = Field(None, ge=1, le=20)


class SourceCitation(BaseModel):
    doc_name: str
    page_number: int
    section_title: str | None
    fiscal_year: str | None
    excerpt: str   # first 200 chars of the chunk


class QueryResponse(BaseModel):
    question: str
    answer: str
    sources: list[SourceCitation]
    retrieval_latency_ms: float
    generation_latency_ms: float
    total_latency_ms: float
    answer_source: str   # "gemini" or "extractive_fallback"


# ── Document listing ──────────────────────────────────────────────────────────

class DocumentInfo(BaseModel):
    doc_id: str
    doc_name: str
    pages: int
    chunks: int
    fiscal_year: str | None
    ingested_at: str   # ISO timestamp
    entity: str | None = None
    # Page counts per detected basis — lets the UI show what was found without
    # re-scanning, and makes a detection failure visible rather than silent.
    standalone_pages: int = 0
    consolidated_pages: int = 0


class DocumentListResponse(BaseModel):
    documents: list[DocumentInfo]
    total: int


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    vector_store_backend: str
    embedding_model: str
    reranker_model: str
    gemini_available: bool
    documents_indexed: int
