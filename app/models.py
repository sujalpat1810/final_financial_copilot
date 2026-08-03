"""
Pydantic schemas for API requests and responses.

Chunk metadata uses conventional field names (text, metadata, page_number) that
line up with what most retrieval tooling expects, so a Chunk is easy to adapt to
another library's document type.  Nothing here depends on one.
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

    def provenance_line(self) -> str:
        """
        One line of natural language describing where this chunk came from,
        prepended to the indexed text so retrieval can match on it.

        When basis is undetermined the clause is omitted rather than filled with
        a label like "financial highlights".  A page outside the statement blocks
        might be the board's report, the ESG section or a ten-year summary, and
        inventing a description for it would both misdescribe the page and let it
        compete with the real statements for a query that names a basis.
        """
        parts = [p for p in (self.entity, self.fiscal_year) if p]
        if not parts:
            return ""
        head = " ".join(parts) + " annual report"
        if self.basis:
            head += f", {self.basis} financial statements"
        return f"{head}, page {self.page_number}."


class Chunk(BaseModel):
    chunk_id: str
    text: str
    metadata: ChunkMetadata

    @property
    def indexed_text(self) -> str:
        """
        What retrieval sees: a provenance line, then the chunk text.

        Measured against the real corpus, the statement pages were unreachable
        without this.  For "What was Infosys consolidated revenue in FY2024-25?",
        the consolidated P&L page scored:

            vector search   not in the top 200 at all
            BM25            rank 118
            reranker        -1.22, against +3.76 for a narrative page

        The reason is that the chunk is a reserialised table of numbers. Nothing
        in its text says "Infosys", "FY2024-25" or "consolidated" — those facts
        live in metadata, which neither the embedder nor the cross-encoder ever
        reads. So the query's most discriminating words could not match the one
        chunk that actually answered it. With the line prepended the same chunk
        scores 6.64, and a "consolidated" query now outranks the standalone page
        rather than being blind to the distinction.

        This is kept SEPARATE from `text` deliberately. `text` is what the answer
        quotes, what the excerpt shows, and what the source viewer searches for
        in the PDF's text layer — and the provenance line does not appear on the
        page, so folding it into `text` would have the viewer hunting for a
        string that cannot be found.
        """
        line = self.metadata.provenance_line()
        return f"{line}\n{self.text}" if line else self.text


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
    excerpt: str

    # ── Addressability ────────────────────────────────────────────────────────
    # Without doc_id the frontend cannot ask for the PDF at all, so a citation
    # could never open its source page.  chunk_id lets the viewer locate the
    # exact chunk rather than re-deriving it from the excerpt.
    doc_id: str | None = None
    chunk_id: str | None = None

    # ── Provenance, carried alongside the page number ─────────────────────────
    # These travel with every citation so a figure is never shown without saying
    # which entity, year and basis it belongs to.  basis is None when it could
    # not be determined from the report's structure; the UI must show that as
    # undetermined rather than resolving it.
    entity: str | None = None
    basis: str | None = None

    # ── Scores ────────────────────────────────────────────────────────────────
    # rerank_score is the raw cross-encoder logit (roughly -11..+11).
    # relevance is a 0-100 display transform of it — monotone, but NOT a
    # probability.  Both are returned so the UI never has to invent a number:
    # the previous frontend faked the score bar as (1 - index/total).
    rerank_score: float | None = None
    relevance: int = 0

    # Chunk text came from a reserialised table ([TABLE] markers, pipe-delimited
    # rows).  Such text will never match the PDF's text layer, so the viewer
    # skips highlight matching for it instead of showing a wrong highlight.
    is_table: bool = False


class QueryResponse(BaseModel):
    question: str
    answer: str
    sources: list[SourceCitation]
    retrieval_latency_ms: float
    generation_latency_ms: float
    total_latency_ms: float
    answer_source: str   # "generated" or "extractive"

    # ── Confidence + abstention ───────────────────────────────────────────────
    # Additive: every pre-existing field above keeps its meaning.
    confidence: str = "insufficient"       # see app/confidence.Confidence
    confidence_reason: str = ""
    abstained: bool = False
    abstention_reason: str | None = None

    # What was searched. Shown on the abstention card: a non-answer that reports
    # its own scope is useful, where a bare "not found" is not.
    documents_searched: int = 0
    chunks_searched: int = 0


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
    # Whether the original PDF is on disk and servable. False for documents
    # ingested before PDFs were persisted, so the UI can present those citations
    # as non-openable instead of offering a link that 404s.
    has_file: bool = False


class DocumentListResponse(BaseModel):
    documents: list[DocumentInfo]
    total: int


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    vector_store_backend: str
    embedding_model: str
    reranker_model: str
    documents_indexed: int
    # Renamed from gemini_available: /docs is public, and naming a third-party
    # model in the health payload invites "where do our documents go?" from
    # confidentiality-conscious firms before there is a good answer ready.
    # The model name stays in config for debugging.
    generation_available: bool
