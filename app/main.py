"""
FastAPI application — entry point.

Run with:
    uvicorn app.main:app --reload

Endpoints:
  POST /ingest                    — upload a PDF for indexing
  POST /query                     — ask a question with optional metadata filters
  GET  /documents                 — list all ingested documents
  GET  /documents/{doc_id}/file   — serve the original PDF, so a citation can
                                    open the page it came from
  GET  /health                    — check service status
  GET  /app                       — the frontend (static files, no build step)

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
import re
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
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import cfg
from app.confidence import assess, relevance
from app.entities import foreign_entities
from app.generation import generate_answer, generation_available
from app.ingestion import (
    AlreadyIndexed,
    ContentConflict,
    get_all_chunks,
    get_document,
    ingest_pdf,
    list_documents,
)
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

    # One throwaway retrieval before serving. Both models lazily initialise on
    # their first real call — measured at roughly an extra 100 ms on the embedder
    # and rather more on the cross-encoder — so without this the first question
    # anyone asks is also the slowest one they will see. It costs a couple of
    # seconds of startup that nobody is watching, to remove them from a moment
    # somebody is.
    if existing_chunks:
        try:
            _state["retriever"].retrieve(query="revenue for the year", top_n=1)
            log.info("Warmed the retrieval path.")
        except Exception as e:  # noqa: BLE001 — a cold first query beats no service
            log.warning("Warmup retrieval failed (%s); serving anyway.", e)

    log.info("Financial Copilot ready.")
    yield
    _state.clear()


app = FastAPI(
    title="Financial Research Copilot",
    # No model vendor in the title or description: /docs is public.
    description="Hybrid retrieval over annual reports, with page-level citations.",
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
        # run_in_threadpool: ingest_pdf parses and chunks synchronously, and a
        # 300-page 30 MB integrated annual report takes minutes. Called directly it
        # would block the event loop for the whole time, so /health and /documents
        # would hang too and the UI would look dead rather than busy.
        doc_id, chunks = await run_in_threadpool(
            ingest_pdf,
            str(tmp_path),
            doc_name or Path(file.filename).stem,
            entity,
            fiscal_year,
        )
    except (AlreadyIndexed, ContentConflict) as e:
        # 409, not 422: the request is well-formed, it conflicts with existing state.
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        # ingest_pdf has already copied the PDF into the store, so discarding the
        # temp upload no longer loses the only copy.
        tmp_path.unlink(missing_ok=True)

    if not chunks:
        raise HTTPException(status_code=422, detail="No text extracted from PDF.")

    # Embed all chunks
    embed: EmbeddingModel = _state["embed"]
    # indexed_text carries the provenance line; see Chunk.indexed_text for the
    # measurements that made it necessary.
    texts = [c.indexed_text for c in chunks]
    embeddings = await run_in_threadpool(embed.embed_documents, texts)

    # Add to vector store (writes the FAISS index + sidecar to disk)
    await run_in_threadpool(_state["vs"].add_chunks, chunks, embeddings)

    # Add to BM25 index. BM25Plus is immutable, so add_chunks re-tokenises and
    # rebuilds the whole corpus — on a multi-document index that is seconds of
    # CPU, not milliseconds, so it does not belong on the event loop either.
    await run_in_threadpool(_state["bm25"].add_chunks, chunks)

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

def _to_citations(results) -> list[SourceCitation]:
    """
    Structured citation data for the frontend.

    Interaction is driven from these fields, not by regex-parsing "[Page N]" out
    of the answer prose.  The inline markers stay in the text for readability, but
    they are not the source of truth for what a citation points at.
    """
    citations = []
    for r in results:
        m = r.chunk.metadata
        citations.append(SourceCitation(
            doc_name=m.doc_name,
            page_number=m.page_number,
            section_title=m.section_title,
            fiscal_year=m.fiscal_year,
            doc_id=m.doc_id,
            chunk_id=m.chunk_id,
            entity=m.entity,
            basis=m.basis,
            rerank_score=r.rerank_score,
            relevance=relevance(r.rerank_score),
            is_table="[TABLE]" in r.chunk.text,
            # Longer than the previous 200 chars: the viewer matches this text
            # against the PDF text layer, and 200 chars is often not a
            # distinctive enough run to locate confidently.
            excerpt=r.chunk.text[:600],
        ))
    return citations


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """Run hybrid retrieval + reranking, then generate — unless evidence is too thin."""
    retriever: HybridRetriever = _state["retriever"]
    vs = _state.get("vs")

    docs = list_documents()
    chunks_searched = vs.get_chunk_count() if vs else 0
    documents_searched = len(docs)

    # Which companies the index actually covers, read per request rather than
    # cached: ingesting a new entity must stop the gate below firing on it
    # without a restart.
    indexed_entities = sorted({d.entity for d in docs if d.entity})
    foreign = foreign_entities(req.question, set(indexed_entities))

    # ── Retrieval ─────────────────────────────────────────────────────────────
    # run_in_threadpool, as /ingest already does: retrieve() is ~2 s of CPU-bound
    # cross-encoder inference. Left on the event loop it blocks every other
    # request for its whole duration — a second question, /documents, and the PDF
    # a citation just tried to open all queue behind it, so one person asking a
    # question freezes the page for everyone else.
    t0 = time.perf_counter()
    results = await run_in_threadpool(
        retriever.retrieve,
        query=req.question,
        top_n=req.top_n,
        filter_doc_name=req.doc_name,
        filter_fiscal_year=req.fiscal_year,
        filter_section_type=req.section_type,
    )
    retrieval_ms = (time.perf_counter() - t0) * 1000

    # ── Abstention gate ───────────────────────────────────────────────────────
    # Assessed BEFORE generation, and generation is skipped entirely when the
    # evidence is below the floor — nothing is generated and then thrown away.
    # An empty result set falls out of this naturally: assess([]) is INSUFFICIENT.
    # Retrieval still runs when `foreign` is non-empty: the near-miss chunks are
    # what the insufficient-evidence card shows, and they are how a reader sees
    # that the tool searched the right documents and simply lacks the company.
    # The expensive half — generation — is what the gate skips.
    assessment = assess(
        [r.rerank_score for r in results],
        foreign_entities=foreign,
        indexed_entities=indexed_entities,
    )

    if assessment.abstained:
        log.info(
            "query='%s' ABSTAINED retrieval=%.0fms (%s)",
            req.question[:60], retrieval_ms, assessment.reason,
        )
        return QueryResponse(
            question=req.question,
            answer="",                      # deliberately empty: no answer exists
            sources=_to_citations(results),  # the near-misses, for the UI to show
            retrieval_latency_ms=round(retrieval_ms, 1),
            generation_latency_ms=0.0,
            total_latency_ms=round(retrieval_ms, 1),
            answer_source="none",
            confidence=assessment.level.value,
            confidence_reason=assessment.reason,
            abstained=True,
            abstention_reason=assessment.abstention_reason,
            documents_searched=documents_searched,
            chunks_searched=chunks_searched,
        )

    # ── Generation ────────────────────────────────────────────────────────────
    # Also off the event loop: this is a blocking HTTPS round trip of 2-20 s,
    # plus up to 3 s of time.sleep() if a transient failure is retried.
    t1 = time.perf_counter()
    answer, answer_source = await run_in_threadpool(
        generate_answer, req.question, results,
    )
    generation_ms = (time.perf_counter() - t1) * 1000

    log.info(
        "query='%s' retrieval=%.0fms generation=%.0fms source=%s confidence=%s",
        req.question[:60], retrieval_ms, generation_ms, answer_source,
        assessment.level.value,
    )

    return QueryResponse(
        question=req.question,
        answer=answer,
        sources=_to_citations(results),
        retrieval_latency_ms=round(retrieval_ms, 1),
        generation_latency_ms=round(generation_ms, 1),
        total_latency_ms=round(retrieval_ms + generation_ms, 1),
        answer_source=answer_source,
        confidence=assessment.level.value,
        confidence_reason=assessment.reason,
        abstained=False,
        documents_searched=documents_searched,
        chunks_searched=chunks_searched,
    )


# ── GET /documents ────────────────────────────────────────────────────────────

@app.get("/documents", response_model=DocumentListResponse)
async def list_docs():
    docs = list_documents()
    return DocumentListResponse(documents=docs, total=len(docs))


# ── GET /documents/{doc_id}/file ──────────────────────────────────────────────

# doc_id is sha256(doc_name)[:16]. Validated rather than trusted: the lookup is a
# dict access so traversal is not reachable, but rejecting junk early keeps a
# malformed id from being reported as "document not found".
_DOC_ID = re.compile(r"^[0-9a-f]{16}$")


@app.get("/documents/{doc_id}/file")
async def get_document_file(doc_id: str):
    """
    Serve the original PDF so a citation can open its source page.

    Inline rather than attachment: the frontend renders this in a side panel with
    PDF.js, and Content-Disposition: attachment would make the browser download it.
    """
    if not _DOC_ID.match(doc_id):
        raise HTTPException(status_code=400, detail="Malformed doc_id.")

    doc = get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Unknown doc_id.")

    path = doc.get("file_path")
    if not path or not Path(path).exists():
        # Distinct from an unknown doc_id: the document is indexed but its PDF is
        # missing — either ingested before PDFs were persisted, or deleted since.
        raise HTTPException(
            status_code=404,
            detail=f"No stored PDF for '{doc['doc_name']}'. Re-ingest it to enable "
                   f"the source viewer.",
        )

    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{doc_id}.pdf"',
            # The stored file is immutable for a given doc_id: a changed document
            # is refused as a ContentConflict rather than overwriting this path.
            "Cache-Control": "private, max-age=3600",
        },
    )


# ── GET /health ───────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health_check():
    vs = _state.get("vs")
    chunk_count = vs.get_chunk_count() if vs else 0
    return HealthResponse(
        status="ok",
        vector_store_backend=cfg.vector_store_backend,
        embedding_model=cfg.embedding_model,
        reranker_model=cfg.reranker_model,
        generation_available=generation_available(),
        documents_indexed=chunk_count,
    )


# ── Frontend ──────────────────────────────────────────────────────────────────
# Mounted LAST and under /app, so it can never shadow an API route: StaticFiles
# at "/" would swallow /query and /documents.
#
# This is why the frontend is served rather than opened from disk. ES modules and
# the PDF.js worker are both blocked over file://, so the module split and the
# source viewer are only possible same-origin. There is still no build step and
# no npm — these are plain static files.
_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND.is_dir():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")
else:
    log.warning("frontend/ not found at %s; UI will not be served.", _FRONTEND)
