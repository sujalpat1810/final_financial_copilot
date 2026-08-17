# Feature 01 — Client as a first-class dimension + filter refactor + bug fixes

**Branch:** `feat/domain-client` · **Depends on:** none · **Status:** done

## Goal

Make the RAG layer client-aware and generically filterable, so every later feature can
filter retrieval by `client`, `doc_type`, and `act_version`. Fix two known bugs while
in the affected files. This is the load-bearing refactor — everything else builds on it.

**Do this feature FIRST and merge it before ANY document is ingested** — it changes
`doc_id` derivation, which invalidates existing ids.

## Files to modify

| File | Change |
|---|---|
| `app/vector_store.py` | ABC `search(query_embedding, top_k, filter_doc_name, filter_fiscal_year)` (~line 37-43) → `search(query_embedding, top_k, filters: dict[str, Any] \| None)`. FAISS impl: post-filter loop (~line 113-125) becomes generic key-match over metadata; keep the `top_k*5` over-fetch. Chroma impl: build native `where` from the dict (~line 224-235); `_scalar_metadata` already drops `None`s. |
| `app/retrieval.py` | `BM25Index.search` filter params (~line 112-139) → same `filters` dict. `HybridRetriever.retrieve` (~line 238-297) signature + pass-through. |
| `app/models.py` | `ChunkMetadata`: add `client: str \| None = None`, `doc_type: str \| None = None`, `act_version: str \| None = None` (~line 30-36). `provenance_line()` (~line 38-55): prepend client name when present — this makes retrieval client-aware through `indexed_text` with zero filter plumbing. `SourceCitation` (~line 141): add `client`, `doc_type`. `QueryRequest` (~line 114-119): add `client`, `doc_type`, `act_version` optional filters. `DocumentInfo` (~line 189): add `client`, `doc_type`. |
| `app/ingestion.py` | Thread `client`/`doc_type`/`act_version` through `ingest_pdf` (~line 287-292, 341-351, 363-375). **`doc_id = sha256(f"{client or ''}/{doc_name}")[:16]`** (~line 308). |
| `app/main.py` | `/ingest`: new optional Form fields `client`, `doc_type`, `act_version` (~line 149-154). `/query` + `/query/stream`: pass new filters (~line 297-304, 339-346). **Bug fix:** `/query` (~line 316-412) duplicates `_prepare()` (~line 274-313) inline — extract to actually call the shared helper, as `main.py:433-435` already claims. |
| `scripts/reindex.py` | **Bug fix:** line ~115 embeds `c.text` — must embed `c.indexed_text` (silently discards the provenance-line ranking advantage measured in `models.py:70-82`). |
| `scripts/ingest.py` | Manifest entries may carry `client`/`doc_type`/`act_version`; pass through (~line 65-88). |

## Steps

1. `filters: dict` refactor in `vector_store.py` (ABC + FAISS + Chroma) — keep
   semantics identical for the two existing filters; commit.
2. Same refactor in `retrieval.py` (BM25 + HybridRetriever); commit.
3. `models.py` new fields + `provenance_line()` client prefix; commit.
4. `ingestion.py` threading + doc_id change; `scripts/ingest.py` manifest fields; commit.
5. `main.py` ingest fields + query filters + `_prepare()` dedupe; commit.
6. `scripts/reindex.py` indexed_text fix; commit.
7. Fix test fallout. Expected to touch: `test_retrieval.py`, `test_vector_store_chroma.py`,
   `test_ingest_metadata.py`, `test_ingest_endpoint.py` (positional-arg binding test!),
   `test_api_contract.py`, `test_query_endpoint.py`. Add new cases: client filter
   respected in FAISS post-filter and Chroma where-clause; provenance line contains
   client; doc_id differs for same doc_name under different clients.

## Acceptance criteria

- Full `pytest` green (was 318 passing; count grows with new cases).
- `provenance_line()` for a chunk with client "Mehta Textiles Pvt Ltd" starts with the
  client name.
- Two ingests, same `doc_name`, different `client` → different `doc_id`, no
  `ContentConflict`.
- `QueryRequest(client=..., doc_type=...)` filters both vector and BM25 lanes.

## Escape hatch (if the dict refactor bleeds past half a day)

Drop the generic dict; ADD named params `filter_client`, `filter_doc_type`,
`filter_act_version` alongside the existing two (uglier, near-zero fixture churn).
Record the choice in Handoff notes.

## Handoff notes

- Went with the full `filters: dict` refactor (no escape hatch needed) — only 3
  test callsites broke, all mechanical.
- Chroma builds `{"$and": [...]}` when >1 condition is active (bare dict for 1) —
  newer chromadb requires this.
- `QueryRequest.retrieval_filters()` is the helper callers should use; section_type
  stays a separate substring param on retrieve().
- Legacy provenance lines are byte-identical (guarded by
  tests/test_client_dimension.py::test_provenance_line_unchanged_for_legacy_documents).
- Bonus fixes landed here: unique temp-upload filename (concurrency), plus the two
  known bugs (reindex indexed_text, /query _prepare dedupe).
- 325 tests green (was 315).
