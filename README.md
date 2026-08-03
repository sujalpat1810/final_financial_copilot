# Financial Research Copilot

An enterprise-grade **Hybrid RAG** system for analyzing annual reports and financial documents.
Generation backend: **Gemini 3.6 Flash** (via the official `google-genai` SDK).
Set with `GEMINI_MODEL`; `gemini-2.5-flash` is retired for new API keys.

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
                        │      Gemini 3.6 Flash                       │
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
│   ├── confidence.py     — score → confidence label; the abstention decision
│   ├── entities.py       — refuses questions about companies that aren't indexed
│   ├── generation.py     — Gemini call, transient retry + extractive fallback
│   └── main.py           — FastAPI routes + startup lifecycle
├── pdf_data/             — source corpus: real annual reports (gitignored)
├── scripts/
│   └── make_basis_fixture.py — regenerates the basis-detection test fixture
├── tests/
│   ├── test_basis_detection.py — standalone/consolidated boundaries vs ground truth
│   ├── test_chunking.py        — unit tests for chunking and heading detection
│   ├── test_confidence.py      — confidence + abstention decision boundaries
│   ├── test_entity_gate.py     — unindexed-company refusals, and their false positives
│   ├── test_ingest_metadata.py — provenance survives ingest onto every chunk
│   ├── test_query_endpoint.py  — /query contract, incl. the abstention gate
│   └── test_retrieval.py       — unit tests for BM25 and merge/dedup logic
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

### 3. Ingest the corpus

Put real annual reports in `pdf_data/` and ingest them before any demo, never
live — a 300-page integrated annual report takes minutes to embed.

`entity` and `fiscal_year` are required and are **not** detected from the
document. An annual report is full of comparative columns, so any heuristic that
reads a year off the page is guessing, and attributing a figure to the wrong
company or year is the failure this tool exists to prevent.

The standalone/consolidated `basis` **is** detected, from the report's own
section structure rather than keywords — see `app/basis.py`. It falls back to
undetermined rather than guessing, and undetermined qualifies the answer.

```bash
python -m scripts.ingest --dry-run    # report what would happen, load no models
python -m scripts.ingest              # ingest everything in the manifest
```

Ingestion is driven by `scripts/corpus.json`, never by globbing `pdf_data/`, so a
file sitting in that directory is not ingested unless it is named in the manifest.
Already-indexed documents with identical content are skipped, so a re-run after an
interruption costs a hash rather than a re-parse.

#### Documents deliberately excluded

**`infosys-ar-99.pdf` (FY1998-99) is not in the manifest and should not be added.**
Two reasons, both measured:

- It is pre-Ind-AS and contains none of the section headings `app/basis.py` relies
  on: **0 markers across all 228 pages**, so every figure from it would be labelled
  "basis not determined".
- Its financials are restated under US, Australian, Canadian, French, German and
  Japanese GAAP, some marked *(Unaudited)*. That contamination is **not** confined
  to one back-section block — `US GAAP` / `Unaudited` appear on 24 pages scattered
  through the document (p2, p49, p73, p109, p190, p212–219, p226 among others), so
  it cannot be fixed by excluding a page range. Serving an unaudited French-GAAP
  figure as "the revenue" is exactly the failure mode this tool exists to prevent,
  and reporting basis is a third axis the `basis` field does not model.

The three-document corpus still exercises every ambiguity the UI must handle:
cross-year (FY2024-25 vs FY2025-26, plus each report's comparative column),
cross-entity (Infosys vs TCS), standalone-vs-consolidated (both, in both), and
abstention (any question about an unindexed company).

### 4. Run the tests

```bash
pytest              # fast: uses the committed basis fixture
pytest -m slow      # re-verifies basis boundaries against the real PDFs (~2 min)
```

### 5. Start the API server

```bash
uvicorn app.main:app --reload
```

API docs are available at `http://localhost:8000/docs`.

---

## API Reference

### `POST /ingest`
Upload a PDF for indexing. `entity` and `fiscal_year` are **required** — they are
never inferred from the document (see *Ingest the corpus* above).

```bash
curl -X POST http://localhost:8000/ingest \
  -F "file=@pdf_data/infosys-ar-25.pdf" \
  -F "doc_name=Infosys FY2024-25" \
  -F "entity=Infosys" \
  -F "fiscal_year=FY2024-25"
```

Returns `409` if a document of that name is already indexed: identical content is
`AlreadyIndexed`, changed content is `ContentConflict`. Re-ingesting is refused
rather than merged, because the vector store cannot delete the old chunks and a
silent second copy could not be undone.

### `POST /query`
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What was Infosys consolidated revenue in FY2024-25?"}'
```

The response is additive over the original shape. Beyond `answer` and `sources`:

| field | meaning |
|---|---|
| `confidence` | `high` / `moderate` / `low` / `insufficient` |
| `confidence_reason` | the arithmetic behind the label, e.g. `top match 6.4, 3 supporting chunks` |
| `abstained` | when true, `answer` is empty and **no generation call was made** |
| `abstention_reason` | what to tell the user |
| `documents_searched`, `chunks_searched` | scope, for the insufficient-evidence card |

Each entry in `sources` carries `doc_id`, `chunk_id`, `entity`, `fiscal_year`,
`basis`, `rerank_score`, `relevance` (0–100 display transform) and `is_table`.
`basis` is `null` when it could not be determined — the UI must show that as
undetermined rather than resolving it.

`answer_source` is `generated` or `extractive`. Deliberately vendor-neutral:
what matters to the reader is whether the answer was synthesised or quoted.

#### Two ways a query abstains

**The score floor** catches questions the corpus has no material on at all.

**The entity gate** (`app/entities.py`) catches questions about a company that
was never indexed — which the score floor provably cannot. Measured over
`data/calibration.json`, four of the six unindexed-company questions scored
*above* the −6.0 floor:

| question | top score | floor catches it? |
|---|---|---|
| melting point of tungsten | −11.20 | yes |
| lunar mining operations | −8.99 | yes |
| HDFC Bank net interest margin | −5.60 | no |
| Reliance Industries headcount | −4.70 | no |
| State Bank of India CAR | −2.44 | no |
| Wipro revenue FY2025 | **+1.33** | no |

Raising the floor cannot close this. Legitimate open questions on the same
corpus run down to −2.24 ("What was the profit for the year?"), so any threshold
that catches Wipro at +1.33 also throws away half the questions the tool exists
to answer. The bands overlap because the reranker measures topical similarity,
and a peer's financial question is topically identical — an Infosys revenue
table really is a good match for "Wipro's revenue". The cross-encoder is not
wrong; it was never asked whether the company matched.

So the gate is categorical rather than scalar: if the question names a company
that is not indexed, no score is high enough to make answering it correct, and
generation is skipped before a single score is read.

It fires only on names it positively recognises — a curated peer gazetteer plus
a corporate-suffix rule ("… Limited", "… Bank"). Unrecognised companies fall
through to the score floor. That asymmetry is deliberate: a false positive
refuses a question the corpus could have answered and looks like a bug, while a
false negative just leaves the previous behaviour in place. A gate keyed on
capitalisation instead would trip over "Ind AS", "March 31, 2025" and "Board of
Directors" — all frequent, none of them companies.

The indexed entity set is read per request, so ingesting Wipro stops the gate
firing on Wipro with no code change.

**Known trade-off:** a question naming both an indexed and an unindexed company
("Compare Infosys and Wipro") abstains entirely rather than answering the half
it can. Answering with Infosys figures alone invites the reader to attribute
them to both.

### `GET /documents`
Lists indexed documents with entity, fiscal year, page and chunk counts,
per-basis page counts, and `has_file` — whether the original PDF is on disk and
so whether citations can open it.

### `GET /documents/{doc_id}/file`
Serves the original PDF inline, so a citation can open the page it came from.
`404` distinguishes an unknown `doc_id` from an indexed document whose PDF is
missing, because the fix differs.

### `GET /health`
Service status, active backend, and whether generation is configured. Reports
`generation_available` rather than naming a model vendor — `/docs` is public.

### `GET /app`
The frontend. Static files, no build step.

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
| Generation | Google Gemini 3.6 Flash | Fast, high-quality, long context; supports citation-aware prompting |
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
| Gemini 3.6 Flash generation | 2–20 s (thinking model; slower than 2.5 Flash) |

Every `/query` response includes `retrieval_latency_ms` and `generation_latency_ms`
so you can quote real numbers from your own run.
