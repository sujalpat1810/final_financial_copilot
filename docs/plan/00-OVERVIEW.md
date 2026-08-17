# CA-Firm AI Demo — Master Plan & Session Tracker

> **READ THIS FILE FIRST in every session.** Then read ONLY your feature's plan file
> (`docs/plan/NN-*.md`). Do not re-read history or other features' files unless your
> Depends-on chain says so. This keeps every session small and cheap.

## Mission

Demo a production-quality AI platform to **3 real Indian CA firms** in a ~15-minute
story: RAG with verifiable citations → deterministic "agent" workflows with
human-in-the-loop → production path → defensible ROI.

Guiding principle: **smallest system, largest demonstrated business value.**
Reliability > flash. Source-backed > hallucinated. Human sign-off > autonomy.

## Locked decisions

- Extend the existing `financial_copilot` hybrid-RAG codebase — do not rebuild.
- LLMs: free-tier **Groq** (primary, llama-3.3-70b, ~1,000 req/day cap — batch calls)
  and **Gemini**; **Claude added as third provider** (activates when an API key exists).
- **No multi-agent / swarm.** Deterministic FastAPI pipelines with constrained LLM
  steps; ONE bounded tool loop only on the Q&A surface. All arithmetic in Python.
- Trust = verifiability, not confidence %: deterministic verification badges,
  field-level provenance with PDF deep-links, source-class labels, exceptions-only
  approval queues, append-only run log ("working-paper trail").
- No live Zoho/Tally integration — roadmap slide only. No auth (local demo).

## Architecture (target)

```
CA (browser) — vanilla-JS SPA, tabs: Ask / Reconcile / Notices
  └─ FastAPI
       ├─ RAG: FAISS+BM25 → cross-encoder → confidence/abstention   [exists]
       │    filters: client, doc_type, act_version, fiscal_year      [feature 01]
       ├─ Deterministic pipelines (SSE stage events):
       │    recon:    load → exact → fuzzy → tolerance → classify → explain(LLM) → queue
       │    casework: extract(LLM) → retrieve statutes → draft(LLM) → APPROVAL GATE
       ├─ SQLite data/ca_demo.db (clients, documents, deadlines,
       │    reco_runs, reco_matches, exceptions, approvals)          [feature 05]
       ├─ Run log: append-only JSONL per run, exportable             [feature 05]
       └─ Providers: Groq | Gemini | Claude via _CALL/_STREAM dicts  [feature 03]
```

## Feature tracker

| # | Plan file | Branch | Depends on | Status |
|---|---|---|---|---|
| 01 | `01-domain-client.md` | `feat/domain-client` | — | **done** |
| 02 | `02-ca-dataset.md` | `feat/ca-dataset` | 01 | **done** |
| 03 | `03-prompts-claude.md` | `feat/prompts-claude` | 02 | **done** |
| 04 | `04-frontend-reskin.md` | `feat/frontend-reskin` | 02 | **done** |
| 05 | `05-recon-engine.md` | `feat/recon-engine` | 02 | **done** |
| 06 | `06-recon-ui.md` | `feat/recon-ui` | 04, 05 | **done** |
| 07 | `07-casework.md` | `feat/casework` | 03, 04 | **done** |
| 08 | `08-dual-act.md` | `feat/dual-act` | 03, 04 | **done** |
| 09 | `09-demo-hardening.md` | `feat/demo-hardening` | 06, 07, 08 | **done** |
| 10 | `10-blueprint-docs.md` | `feat/blueprint-docs` | — (docs only) | **done** |

Day mapping (3-day target): 01+02 = Day 1 AM · 03+04 = Day 1 PM · 05 = Day 2 AM ·
06 = Day 2 PM · 07+08 = Day 3 AM · 09 = Day 3 PM (FEATURE FREEZE before 09) ·
10 fills gaps any time.

**Cut order if behind:** client briefing (stretch, in 08) → "correct" action in
review queue (keep accept/reject) → dual-Act UI toggle (fall back to two narrated
sequential queries) → full notice draft (fall back to discrepancy + cited provisions;
rehearse a pre-generated known-good draft). **Never cut:** Q&A, recon engine + queue,
run log, trace.

## Session protocol (follow exactly)

1. `git checkout ca-demo` and `git pull origin ca-demo`.
2. `git checkout -b <feature-branch>` (name from the tracker).
3. Read this file + your feature file. Nothing else unless Depends-on requires it.
4. Execute the feature's steps **in order**. Small commits per logical step.
5. Before finishing: run the feature's own tests AND the full `pytest` suite.
   All green or the feature does not merge.
6. `git checkout ca-demo && git merge <feature-branch> && git push origin ca-demo`.
7. Update your feature file: set Status to `done`, fill **Handoff notes** (surprises,
   deviations, anything the next session must know). Update the tracker row above.
   Commit these doc updates to `ca-demo` and push.
8. Never commit to `main`. Never force-push. `data/ca_dataset/` generated files ARE
   committed (demo fixture); `data/ca_demo.db` is gitignored and rebuilt by seed script.

## Environment facts every session needs

- Python venv: `../../../.venv/Scripts/python.exe` relative to repo root (worktree
  sessions) or `.venv/Scripts/python.exe` (main checkout). 318 tests pass at baseline.
- Run tests: `python -m pytest -q` (fast) · `-m "slow or not slow"` (with real PDFs).
- Run app: `uvicorn app.main:app --host 127.0.0.1 --port 8000` → UI at `/app`.
- `.env` has GROQ/GEMINI keys, `VECTOR_STORE_BACKEND=faiss`, `GENERATION_PROVIDER=groq`.
- Store paths: `data/chunk_store.json`, `data/faiss_index*`, `data/pdf_store/`.

## Demo dataset (spec — built in feature 02)

Two fictional clients: **Mehta Textiles Pvt Ltd** and **Sharma Electronics Pvt Ltd**.
Under `data/ca_dataset/`: purchase register CSV (~40 rows) · GSTR-2B CSV (~35 rows,
planted mismatches across all 4 buckets + 1 duplicate + 1 amendment) · 5-6 GST invoice
PDFs (1-2 planted errors: invalid GSTIN checksum, line-sum≠total) · 1 ASMT-10 notice
PDF · prior-year financials PDF · CGST Act extracts (s.16, s.61+Rule 99, s.73/74/75) ·
one income-tax provision in BOTH 1961-Act and 2025-Act numbering · SQLite seed.
Generator script asserts expected recon outcomes. Dataset FROZEN once feature 02 merges.

## Demo storyline (~15 min — full script in docs/DEMO_SCRIPT.md, feature 10)

1. Trust story: ITAT Bengaluru fake-citation recall; Deloitte Australia refund →
   "this system never invents a source; watch it refuse."
2. Q&A: statute question → cited answer → click → PDF opens highlighted. Then an
   un-indexed client question → abstains.
3. Dual-Act: same provision, 1961 vs 2025 numbering side by side.
4. Recon agent: run → live trace → badges → exceptions queue → accept one, reject
   one → export run log ("your working-paper trail").
5. Notice→Reply: ASMT-10 → discrepancy → drafted ASMT-11 with citations → approval
   gate ("nothing leaves without a CA's sign-off").
6. ROI: conservative 10-30% (RCT), optimistic 38-115% (RAG-arm RCT, labelled);
   rupee bridge via ICAI fee calculator; 2,520 GST reco cycles/yr ≈ 3 FTE (attributed
   practitioner estimate). Roadmap: pilot → Tally/Zoho ingest → DPDP-ready production.
