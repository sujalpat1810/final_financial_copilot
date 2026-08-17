# Feature 05 — Reconciliation engine (backend vertical)

**Branch:** `feat/recon-engine` · **Depends on:** 02 · **Status:** pending

## Goal

The demo's core: deterministic GSTR-2B ↔ purchase-register reconciliation with
verification checks, exception classification, per-exception LLM explanation, an
approval workflow, and an immutable run log. **All matching and arithmetic in pure
Python — the LLM only explains, never computes.**

## New files

| File | Contents |
|---|---|
| `app/verify.py` | Pure functions: `gstin_checksum_valid(gstin)` (15-char; mod-36 with alternating 1/2 weights on char values, standard GSTIN algorithm — TEST-DRIVE THIS against known-valid public GSTINs + the dataset's planted invalid one); `tax_split_consistent(cgst, sgst, igst)` (intra-state → CGST=SGST and IGST=0; inter-state → IGST only); `lines_sum_to_total(lines, total, tol=0.01)`; `rate_in_slabs(rate)` (0/0.1/0.25/1/1.5/3/5/7.5/12/18/28 — accept common ones). |
| `app/structured.py` | SQLite access layer (stdlib `sqlite3`), DB at `cfg.ca_db_path` (new config field, default `data/ca_demo.db`, gitignored). DDL below, `init_db()`, seed-from-JSON, CRUD for runs/matches/exceptions/approvals. All calls made through threadpool at route layer (existing discipline). |
| `app/recon.py` | Pure functions over row dicts (NO fastapi/sqlite imports): `load_register(path)`, `load_gstr2b(path)`, `normalize_row` (strip/upper GSTIN, canonical invoice_no, parse amounts), then pipeline: `match_exact` (GSTIN+invoice_no+total) → `match_fuzzy` (GSTIN edit-distance ≤1 OR vendor-name `difflib.SequenceMatcher` ratio ≥0.85, then invoice_no+amount) → `match_tolerance` (matched keys, amount delta ≤ ₹1 rounding) → `classify_exceptions` → 4 buckets: `in_2b_not_books`, `in_books_not_2b`, `amount_mismatch`, `gstin_mismatch`; plus duplicate detection (same GSTIN+invoice_no twice in books) and amendment handling (B2BA row supersedes original — match against amended value). Returns a `ReconResult` dataclass (matches, exceptions, stats). |
| `app/runlog.py` | `RunLog(run_id)`: append-only JSONL at `data/runs/{run_id}.jsonl` — events `{ts, stage, detail, counts, model?, prompt_version?}`. Never mutate, never rewrite. |
| `app/routes_recon.py` | APIRouter: `POST /recon/run` (body: client_id, period → SSE stream reusing the existing meta/delta/done frame builder from `main.py:417` — stages: load → verify → match_exact → match_fuzzy → tolerance → classify → explain → done); `GET /recon/runs`, `GET /recon/runs/{id}`, `GET /recon/runs/{id}/exceptions`; `POST /recon/exceptions/{id}/decision` (action: accepted/corrected/rejected + note → writes `approvals` row + updates exception status); `GET /recon/runs/{id}/log` (returns the JSONL as a download). Mount via `app.include_router` in `main.py`. |

## SQLite DDL

```sql
clients      (client_id TEXT PRIMARY KEY, name TEXT, gstin TEXT, pan TEXT, state_code TEXT);
documents    (doc_id TEXT PRIMARY KEY, client_id TEXT, doc_name TEXT, doc_type TEXT,
              fiscal_year TEXT, act_version TEXT, path TEXT, created_at TEXT);
deadlines    (id INTEGER PRIMARY KEY, client_id TEXT, form TEXT, period TEXT,
              due_date TEXT, status TEXT);
reco_runs    (run_id TEXT PRIMARY KEY, client_id TEXT, period TEXT,
              books_path TEXT, gstr2b_path TEXT, params_json TEXT,
              status TEXT, started_at TEXT, finished_at TEXT, log_path TEXT);
reco_matches (match_id INTEGER PRIMARY KEY, run_id TEXT, books_row_json TEXT,
              g2b_row_json TEXT, match_type TEXT, score REAL);
exceptions   (exc_id INTEGER PRIMARY KEY, run_id TEXT, bucket TEXT,
              books_row_json TEXT, g2b_row_json TEXT, delta_json TEXT,
              llm_explanation TEXT, llm_model TEXT, status TEXT DEFAULT 'open');
approvals    (approval_id INTEGER PRIMARY KEY, subject_type TEXT, subject_id TEXT,
              action TEXT, note TEXT, actor TEXT, created_at TEXT);
```

## LLM explanations

After classification: ONE `recon_explain` call (feature 03 prompt) per exception
(~12 total). **Batch-aware:** sequential with the existing retry scaffolding; if the
provider fails → store the deterministic bucket description as the explanation
(extractive-fallback philosophy). Tag stored explanation with model id.

## Verification badges (returned in run stats, rendered by feature 06)

Per-row checks from `verify.py` aggregated: `{gstin_valid: n/N, tax_split_ok: n/N,
totals_ok: n/N}` + per-exception check results in `delta_json`.

## Steps

1. `tests/test_verify.py` FIRST, then `app/verify.py` until green. Commit.
2. `app/structured.py` + `init_db` + seed loading (`data/ca_dataset/seed/`). Commit.
3. `app/recon.py` + `tests/test_recon.py` — **assert the exact planted outcome:
   `from scripts.make_ca_dataset import EXPECTED`** (bucket counts, duplicate found,
   amendment resolved). This test is the demo's insurance policy. Commit.
4. `app/runlog.py` (+ small test: append-only, valid JSONL). Commit.
5. `app/routes_recon.py` + SSE + endpoint tests (TestClient with stubbed generation,
   following `test_query_endpoint.py`'s stub pattern). Mount in `main.py`. Commit.
6. LLM explanations wiring. Full pytest. Commit.
7. Manual: `curl -N POST /recon/run` → watch SSE stages; check `data/runs/*.jsonl`.

## Acceptance criteria

- `test_recon.py` pins generator's EXPECTED exactly.
- Run end-to-end via API: exceptions stored, decisions recorded in `approvals`,
  log downloadable, re-running creates a NEW run (runs are immutable history).
- Full pytest green. `data/ca_demo.db` in `.gitignore`; `data/runs/` gitignored too.

## Handoff notes

_(fill at session end)_
