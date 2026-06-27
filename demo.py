"""
End-to-end demo script for the Financial Research Copilot.

Usage:
  # From the financial_copilot/ directory:
  python demo.py

What it does:
  1. Converts the sample .txt annual report to a PDF (requires reportlab).
  2. Ingests the PDF via the ingestion pipeline (directly, not via HTTP).
  3. Runs 3 example queries demonstrating hybrid retrieval + reranking.
  4. Prints retrieved chunks + final answer (Gemini if GEMINI_API_KEY is set,
     extractive fallback otherwise).

Note: This script imports the app modules directly — the FastAPI server does
NOT need to be running.  It's a standalone verification script.
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

# Ensure the project root is on the path so `app.*` imports work
sys.path.insert(0, str(Path(__file__).parent))

# Force UTF-8 output on Windows (avoids cp1252 encode errors for unicode chars)
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Load .env before importing config (config reads os.environ at import time)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SAMPLE_TXT = Path("data/sample_reports/acme_annual_report_2023.txt")
SAMPLE_PDF = Path("data/sample_reports/acme_annual_report_2023.pdf")


# ── Step 0: generate the sample PDF if it doesn't exist ──────────────────────

def ensure_sample_pdf() -> Path:
    if SAMPLE_PDF.exists():
        print(f"[demo] Using existing sample PDF: {SAMPLE_PDF}")
        return SAMPLE_PDF

    print("[demo] Generating sample PDF from .txt (requires reportlab)…")
    try:
        from data.sample_reports.generate_sample_pdf import txt_to_pdf
        txt_to_pdf(SAMPLE_TXT, SAMPLE_PDF)
        return SAMPLE_PDF
    except Exception as e:
        print(f"[demo] Could not generate PDF ({e}).")
        print("[demo] Trying to use the .txt file as a fallback via a dummy PDF wrapper…")
        return _txt_as_pdf_fallback()


def _txt_as_pdf_fallback() -> Path:
    """If reportlab isn't installed, create a minimal single-page PDF from the text."""
    try:
        import struct, zlib

        text = SAMPLE_TXT.read_text(encoding="utf-8", errors="replace")
        # pypdf / pdfplumber can't read raw text, so we build the simplest possible PDF
        # that embeds the text as a stream.  This is a well-known minimal PDF recipe.
        lines = text.replace("(", r"\(").replace(")", r"\)").split("\n")
        # Encode up to 200 lines per page as PDF text commands
        pdf_lines = []
        for line in lines[:200]:
            pdf_lines.append(f"({line}) Tj T*")
        content = "BT /F1 9 Tf 50 750 Td 12 TL\n" + "\n".join(pdf_lines) + "\nET"

        content_bytes = content.encode("latin-1", errors="replace")

        obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        obj3 = b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        obj4 = (
            b"4 0 obj\n<< /Length " + str(len(content_bytes)).encode() + b" >>\nstream\n"
            + content_bytes + b"\nendstream\nendobj\n"
        )
        obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj\n"

        xref_pos = len(b"%PDF-1.4\n") + len(obj1) + len(obj2) + len(obj3) + len(obj4) + len(obj5)
        body = b"%PDF-1.4\n" + obj1 + obj2 + obj3 + obj4 + obj5
        trailer = (
            b"xref\n0 6\n0000000000 65535 f \n"
            + b"xref placeholder\n"
            + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
            + str(xref_pos).encode() + b"\n%%EOF\n"
        )
        SAMPLE_PDF.write_bytes(body + trailer)
        print(f"[demo] Minimal fallback PDF written to {SAMPLE_PDF}")
        return SAMPLE_PDF
    except Exception as e:
        print(f"[demo] Fallback PDF generation failed: {e}")
        print("[demo] Please install reportlab: pip install reportlab")
        sys.exit(1)


# ── Step 1: ingest ────────────────────────────────────────────────────────────

def run_ingestion(pdf_path: Path) -> list:
    from app.ingestion import ingest_pdf
    from app.retrieval import EmbeddingModel
    from app.vector_store import get_vector_store

    print(f"\n{'='*60}")
    print("STEP 1: INGESTION")
    print(f"{'='*60}")
    print(f"Parsing: {pdf_path}")

    doc_id, chunks = ingest_pdf(str(pdf_path), "Acme Corp 10-K FY2023")
    print(f"  doc_id        : {doc_id}")
    print(f"  chunks created: {len(chunks)}")
    if chunks:
        print(f"  sample chunk  : {chunks[0].text[:120]}...")
        print(f"  metadata      : {chunks[0].metadata}")
    return chunks


# ── Step 2: build indexes ─────────────────────────────────────────────────────

def build_indexes(chunks):
    from app.retrieval import BM25Index, EmbeddingModel, Reranker, HybridRetriever
    from app.vector_store import get_vector_store

    print(f"\n{'='*60}")
    print("STEP 2: BUILDING INDEXES")
    print(f"{'='*60}")

    print("  Loading embedding model (all-MiniLM-L6-v2)…")
    embed = EmbeddingModel()

    print("  Embedding all chunks…")
    t0 = time.perf_counter()
    embeddings = embed.embed_documents([c.text for c in chunks])
    print(f"  Embedded {len(embeddings)} chunks in {(time.perf_counter()-t0)*1000:.0f} ms")

    print("  Indexing into vector store…")
    vs = get_vector_store()
    vs.add_chunks(chunks, embeddings)
    print(f"  Vector store now contains {vs.get_chunk_count()} chunks")

    print("  Building BM25 index…")
    bm25 = BM25Index()
    bm25.build(chunks)
    print(f"  BM25 index built with {len(chunks)} chunks")

    print("  Loading cross-encoder reranker…")
    reranker = Reranker()

    retriever = HybridRetriever(
        vector_store=vs,
        bm25_index=bm25,
        embedding_model=embed,
        reranker=reranker,
    )
    return retriever


# ── Step 3: queries ───────────────────────────────────────────────────────────

QUERIES = [
    {
        "question": "What was Acme Corporation's total revenue in fiscal year 2023, and how did it compare to 2022?",
        "filters": {},
    },
    {
        "question": "What are the key risk factors mentioned in the annual report?",
        "filters": {},
    },
    {
        "question": "What was the cash and cash equivalents balance on the balance sheet as of December 31, 2023?",
        "filters": {},
    },
]


def run_queries(retriever):
    from app.generation import generate_answer

    print(f"\n{'='*60}")
    print("STEP 3: QUERIES")
    print(f"{'='*60}")

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        print("  GEMINI_API_KEY detected — answers will use Gemini 2.5 Flash")
    else:
        print("  No GEMINI_API_KEY — using extractive fallback (set the env var for Gemini)")

    for i, q in enumerate(QUERIES, 1):
        print(f"\n{'-'*60}")
        print(f"Query {i}: {q['question']}")
        print("─"*60)

        t0 = time.perf_counter()
        results = retriever.retrieve(query=q["question"], top_n=4, **q["filters"])
        retrieval_ms = (time.perf_counter() - t0) * 1000
        print(f"  Retrieved {len(results)} chunks in {retrieval_ms:.0f} ms")

        for j, r in enumerate(results, 1):
            src = r.retrieval_source
            page = r.chunk.metadata.page_number
            rerank = f"{r.rerank_score:.3f}" if r.rerank_score is not None else "n/a"
            print(f"  [{j}] Page {page} | source={src} | rerank={rerank}")
            print(f"      {r.chunk.text[:120].strip()}…")

        t1 = time.perf_counter()
        answer, source = generate_answer(q["question"], results)
        gen_ms = (time.perf_counter() - t1) * 1000

        print(f"\n  ANSWER (source={source}, {gen_ms:.0f} ms):")
        print("  " + "\n  ".join(answer.strip().split("\n")))


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Financial Research Copilot - End-to-End Demo")
    print("=" * 60)

    pdf_path = ensure_sample_pdf()
    chunks = run_ingestion(pdf_path)
    retriever = build_indexes(chunks)
    run_queries(retriever)

    print(f"\n{'='*60}")
    print("Demo complete. Start the API with:")
    print("  uvicorn app.main:app --reload")
    print("Then POST to http://localhost:8000/ingest and /query")
    print("=" * 60)
