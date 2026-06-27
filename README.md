# Financial Research Copilot

An enterprise-grade **Hybrid RAG** system for analyzing annual reports and financial documents.
Generation backend: **Gemini 2.5 Flash** (via the official `google-genai` SDK).

---

## Architecture Diagram

```
                        ┌─────────────────────────────────────────────┐
                        │              FastAPI Backend                 │
  PDF Upload ──────────►│  POST /ingest                               │
                        │    │                                         │
                        │    ▼                                         │
                        │  pdfplumber / pypdf                         │
                        │  (page-by-page text + table extraction)     │
                        │    │                                         │
                        │    ▼                                         │
                        │  Semantic Chunker                           │
                        │  (paragraph boundaries + overlap)           │
                        │    │                                         │
                        │    ├──────────────────────┐                 │
                        │    ▼                      ▼                 │
                        │  Sentence-Transformer   rank_bm25           │
                        │  Embeddings             Tokenizer           │
                        │    │                      │                 │
                        │    ▼                      ▼                 │
                        │  FAISS / ChromaDB       BM25 Index         │
                        │  (Vector Store)         (Keyword Store)     │
                        └────────────────────────────────────────────-┘

  Question ────────────►┌─────────────────────────────────────────────┐
  + Filters             │  POST /query                                │
                        │    │                                         │
                        │    ├──────────────┬──────────────┐          │
                        │    ▼              ▼              │          │
                        │  Embed Query   BM25 Search      │          │
                        │  → Vector      top-k by         │          │
                        │    Search      keyword score    │          │
                        │    top-k by                     │          │
                        │    cosine sim                   │          │
                        │    │              │              │          │
                        │    └──────────────┘              │          │
                        │           │                      │          │
                        │           ▼                      │          │
                        │      Merge + Dedupe              │          │
                        │      (by chunk_id)               │          │
                        │           │                      │          │
                        │           ▼                      │          │
                        │      Cross-Encoder Reranker      │          │
                        │      (ms-marco-MiniLM-L-6-v2)   │          │
                        │           │                      │          │
                        │           ▼                      │          │
                        │      Top-N Chunks + Citations    │          │
                        │           │                                  │
                        │           ▼                                  │
                        │      Gemini 2.5 Flash                       │
                        │      (with page-citation prompt)            │
                        │           │                                  │
                        │           ▼                                  │
                        │      Answer + Sources                        │
                        └─────────────────────────────────────────────┘
```

---

## How It Works — Key Concepts for Interviews

### What is Hybrid Retrieval?

Standard RAG uses only **vector (semantic) search**: a query is embedded into a dense
vector, and chunks are ranked by cosine similarity to that vector.  This works well for
paraphrase-style retrieval ("how did earnings perform?") but has a critical blind spot:
**it can miss or garble exact numbers, table values, and domain-specific terms**.

Hybrid retrieval adds **BM25 keyword search** (a probabilistic bag-of-words ranker) as a
second lane, then merges both result sets and reranks the union with a cross-encoder.

### Why Vectorless / Page-Index Retrieval Matters for Financial Documents

Financial documents have several properties that break pure semantic search:

| Property | Why semantic search struggles | Why BM25 helps |
|---|---|---|
| Exact numbers | "Revenue of $12.4B" embeds similarly to "$12.4M" | BM25 matches the literal token "12.4B" |
| Table cells | Tables flatten to whitespace-separated tokens that lose structure | BM25 still finds "EBITDA \| 3,400" |
| Ticker symbols / line-item names | Rare tokens often collapse to generic embeddings | BM25 treats them as high-IDF (high-weight) terms |
| Fiscal-year specificity | "FY2022 revenue" and "FY2023 revenue" have near-identical embeddings | BM25 hard-matches the year token |

By running both searches and reranking the merged set with a cross-encoder (which sees the
full query + passage together), we get:
- **Semantic recall** for concept-level questions
- **Keyword precision** for exact values and table lookups
- **Cross-encoder accuracy** for the final ranking decision

---

## Project Structure

```
financial_copilot/
├── app/
│   ├── config.py         — all knobs in one place; env vars override defaults
│   ├── models.py         — Pydantic schemas (request/response/chunk/citation)
│   ├── ingestion.py      — PDF parse → semantic chunk → persist to JSON store
│   ├── vector_store.py   — FAISS + ChromaDB behind a shared VectorStore ABC
│   ├── retrieval.py      — EmbeddingModel, BM25Index, Reranker, HybridRetriever
│   ├── generation.py     — Gemini 2.5 Flash call + extractive fallback
│   └── main.py           — FastAPI routes + startup lifecycle
├── data/
│   └── sample_reports/
│       ├── acme_annual_report_2023.txt   — synthetic 10-K for testing
│       └── generate_sample_pdf.py        — converts .txt → PDF (needs reportlab)
├── tests/
│   ├── test_chunking.py  — unit tests for chunking and metadata extraction
│   └── test_retrieval.py — unit tests for BM25 and merge/dedup logic
├── demo.py               — standalone end-to-end script (no server needed)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Setup

### 1. Install dependencies

> **Python version**: PyTorch officially supports Python **3.9–3.12**.  
> If you are on Python 3.13 or 3.14, install the CPU-only torch wheel first:
> ```bash
> pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
> ```
> Using Python 3.11 or 3.12 avoids this constraint entirely.

```bash
# Create and activate a virtual environment (recommended)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

> **First run**: sentence-transformers will download `all-MiniLM-L6-v2` (~80 MB)
> and the cross-encoder `ms-marco-MiniLM-L-6-v2` (~25 MB) automatically.

### 2. Set your Gemini API key (optional)

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=your_actual_key
```

If you skip this step the system works fine — it returns the top retrieved chunks
directly instead of a generated answer (the "extractive fallback").

### 3. (Optional) Generate the sample PDF

```bash
pip install reportlab
python data/sample_reports/generate_sample_pdf.py
```

### 4. Run the demo script

```bash
python demo.py
```

This ingests the sample report and runs 3 example queries end-to-end, printing
retrieved chunks and the final answer.  No server needed.

### 5. Start the API server

```bash
uvicorn app.main:app --reload
```

API docs are available at `http://localhost:8000/docs`.

---

## API Reference

### `POST /ingest`
Upload a PDF for indexing.

```bash
curl -X POST http://localhost:8000/ingest \
  -F "file=@path/to/annual_report.pdf" \
  -F "doc_name=Apple 10-K FY2023"
```

### `POST /query`
Ask a question with optional filters.

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What was total revenue in FY2023?",
    "fiscal_year": "FY2023",
    "doc_name": "Apple 10-K FY2023",
    "top_n": 5
  }'
```

### `GET /documents`
List all ingested documents.

### `GET /health`
Check service status, active backend, and whether Gemini is available.

---

## Switching Vector Store Backend

Edit `.env`:

```bash
VECTOR_STORE_BACKEND=chroma   # or "faiss" (default)
```

Both backends implement the same `VectorStore` ABC in `vector_store.py`, so no
other code changes are needed.

**FAISS** — better for: fast prototyping, single-node, in-memory speed.  
**ChromaDB** — better for: built-in metadata filtering, persistent SQLite storage,
easier to inspect with the Chroma browser.

---

## Running Tests

```bash
pytest tests/ -v
```

The tests cover:
- Semantic chunker: splitting, overlap, table marker preservation
- Metadata extraction: fiscal year detection, section heading detection
- BM25 index: build, search, filter by doc/year, incremental add
- Merge/dedup logic: hybrid result fusion, source tagging

---

## Component Technology Choices

| Component | Library | Why |
|---|---|---|
| PDF parsing | pdfplumber + pypdf | pdfplumber extracts tables as structured rows; pypdf is the fast fallback |
| Embeddings | sentence-transformers | Free, local, no API key; all-MiniLM-L6-v2 is fast and accurate |
| Vector store | FAISS / ChromaDB | FAISS for speed; Chroma for richer metadata queries — both behind one interface |
| Keyword search | rank-bm25 | Lightweight pure-Python BM25Okapi; no server, no index format lock-in |
| Reranker | CrossEncoder (sentence-transformers) | Cross-encoders are far more accurate than bi-encoders for final ranking |
| Orchestration | LangChain | EmbeddingModel implements LangChain's Embeddings interface for composability |
| Document store | LlamaIndex | Page-level node abstraction with metadata; used for the chunk store design |
| Generation | Google Gemini 2.5 Flash | Fast, high-quality, long context; supports citation-aware prompting |
| API | FastAPI | Async, automatic OpenAPI docs, native Pydantic integration |

---

## Latency Benchmarks (approximate, CPU-only)

| Stage | Typical latency |
|---|---|
| PDF ingestion (10-page doc) | 2–5 s |
| Embedding 50 chunks | 1–3 s |
| Vector search (top-10, 500 chunks) | < 5 ms |
| BM25 search (top-10, 500 chunks) | < 2 ms |
| Cross-encoder rerank (20 candidates) | 200–600 ms |
| Gemini 2.5 Flash generation | 1–4 s |

Every `/query` response includes `retrieval_latency_ms` and `generation_latency_ms`
so you can quote real numbers from your own run.
