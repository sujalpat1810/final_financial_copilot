"""
FastAPI application — entry point.

Run with:
    uvicorn app.main:app --reload

Endpoints:
  POST /ingest          — upload a PDF for indexing
  POST /query           — ask a question with optional metadata filters
  GET  /documents       — list all ingested documents
  GET  /health          — check service status

Startup sequence:
  1. Instantiate the embedding model (downloads ~80 MB on first run).
  2. Instantiate the cross-encoder reranker (~25 MB).
  3. Instantiate the configured vector store (FAISS or Chroma).
  4. Build the BM25 index from any chunks already persisted on disk
     (so the service survives restarts without re-ingesting everything).
  5. Wire all components into a single HybridRetriever.

Latency logging:
  Every /query response includes retrieval_latency_ms and generation_latency_ms
  so you can quote real numbers ("retrieval takes ~N ms, generation ~M ms").
"""

from __future__ import annotations

import io
import time
import logging

# Load .env file if present (must happen before config.py reads os.environ)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import cfg
from app.generation import generate_answer
from app.ingestion import get_all_chunks, ingest_pdf, list_documents
from app.models import (
    DocumentListResponse,
    HealthResponse,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    SourceCitation,
)
from app.retrieval import BM25Index, EmbeddingModel, HybridRetriever, Reranker
from app.vector_store import get_vector_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Application state (singletons shared across requests) ─────────────────────

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Loading embedding model: %s", cfg.embedding_model)
    _state["embed"] = EmbeddingModel()

    log.info("Loading reranker: %s", cfg.reranker_model)
    _state["reranker"] = Reranker()

    log.info("Initialising vector store: %s", cfg.vector_store_backend)
    _state["vs"] = get_vector_store()

    log.info("Building BM25 index from persisted chunks…")
    bm25 = BM25Index()
    existing_chunks = get_all_chunks()
    if existing_chunks:
        bm25.build(existing_chunks)
        log.info("BM25 index built from %d existing chunks.", len(existing_chunks))
    _state["bm25"] = bm25

    _state["retriever"] = HybridRetriever(
        vector_store=_state["vs"],
        bm25_index=_state["bm25"],
        embedding_model=_state["embed"],
        reranker=_state["reranker"],
    )

    log.info("Financial Copilot ready.")
    yield
    _state.clear()


app = FastAPI(
    title="Financial Research Copilot",
    description="Hybrid RAG pipeline for annual report analysis (Gemini 2.5 Flash)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── POST /ingest ──────────────────────────────────────────────────────────────

@app.post("/ingest", response_model=IngestResponse)
async def ingest(
    file: UploadFile = File(..., description="PDF annual report"),
    doc_name: str | None = Form(None, description="Override document name"),
    entity: str = Form(..., description="Reporting entity, e.g. 'Infosys'"),
    fiscal_year: str = Form(..., description="Fiscal year of the report, e.g. 'FY2024-25'"),
):
    """
    Parse a PDF, chunk it, and add it to both vector and BM25 indexes.

    entity and fiscal_year are required.  They are not inferred from the
    document: an annual report is full of comparative columns, so any heuristic
    that reads a year off the page is guessing.  Getting these wrong attributes
    a figure to the wrong company or year, which is the failure this product
    exists to prevent — so they are asked for rather than assumed.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    entity = entity.strip()
    fiscal_year = fiscal_year.strip()
    if not entity or not fiscal_year:
        raise HTTPException(status_code=400, detail="entity and fiscal_year must not be blank.")

    # Save upload to a temp file
    tmp_path = Path("data") / "tmp_upload.pdf"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    tmp_path.write_bytes(content)

    try:
        doc_id, chunks = ingest_pdf(
            str(tmp_path),
            doc_name or Path(file.filename).stem,
            entity=entity,
            fiscal_year=fiscal_year,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    if not chunks:
        raise HTTPException(status_code=422, detail="No text extracted from PDF.")

    # Embed all chunks
    embed: EmbeddingModel = _state["embed"]
    texts = [c.text for c in chunks]
    embeddings = embed.embed_documents(texts)

    # Add to vector store
    _state["vs"].add_chunks(chunks, embeddings)

    # Add to BM25 index (rebuild in-memory)
    _state["bm25"].add_chunks(chunks)

    log.info("Ingested '%s': %d chunks across %d pages", doc_name, len(chunks),
             len({c.metadata.page_number for c in chunks}))

    pages = len({c.metadata.page_number for c in chunks})
    return IngestResponse(
        doc_id=doc_id,
        doc_name=chunks[0].metadata.doc_name,
        pages_processed=pages,
        chunks_created=len(chunks),
        message=f"Successfully indexed {len(chunks)} chunks from {pages} pages.",
    )


# ── POST /query ───────────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """Run hybrid retrieval + reranking + Gemini generation."""
    retriever: HybridRetriever = _state["retriever"]

    # ── Retrieval ─────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    results = retriever.retrieve(
        query=req.question,
        top_n=req.top_n,
        filter_doc_name=req.doc_name,
        filter_fiscal_year=req.fiscal_year,
        filter_section_type=req.section_type,
    )
    retrieval_ms = (time.perf_counter() - t0) * 1000

    if not results:
        return QueryResponse(
            question=req.question,
            answer="No relevant documents found. Please ingest some PDFs first.",
            sources=[],
            retrieval_latency_ms=round(retrieval_ms, 1),
            generation_latency_ms=0.0,
            total_latency_ms=round(retrieval_ms, 1),
            answer_source="none",
        )

    # ── Generation ────────────────────────────────────────────────────────────
    t1 = time.perf_counter()
    answer, answer_source = generate_answer(req.question, results)
    generation_ms = (time.perf_counter() - t1) * 1000

    log.info(
        "query='%s' retrieval=%.0fms generation=%.0fms source=%s",
        req.question[:60], retrieval_ms, generation_ms, answer_source,
    )

    sources = [
        SourceCitation(
            doc_name=r.chunk.metadata.doc_name,
            page_number=r.chunk.metadata.page_number,
            section_title=r.chunk.metadata.section_title,
            fiscal_year=r.chunk.metadata.fiscal_year,
            excerpt=r.chunk.text[:200],
        )
        for r in results
    ]

    return QueryResponse(
        question=req.question,
        answer=answer,
        sources=sources,
        retrieval_latency_ms=round(retrieval_ms, 1),
        generation_latency_ms=round(generation_ms, 1),
        total_latency_ms=round(retrieval_ms + generation_ms, 1),
        answer_source=answer_source,
    )


# ── GET /documents ────────────────────────────────────────────────────────────

@app.get("/documents", response_model=DocumentListResponse)
async def list_docs():
    docs = list_documents()
    return DocumentListResponse(documents=docs, total=len(docs))


# ── GET /health ───────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health():
    vs = _state.get("vs")
    chunk_count = vs.get_chunk_count() if vs else 0
    return HealthResponse(
        status="ok",
        vector_store_backend=cfg.vector_store_backend,
        embedding_model=cfg.embedding_model,
        reranker_model=cfg.reranker_model,
        gemini_available=bool(cfg.gemini_api_key),
        documents_indexed=chunk_count,
    )
