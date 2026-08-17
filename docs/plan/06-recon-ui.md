# Feature 06 — Reconciliation UI + agent trace

**Branch:** `feat/recon-ui` · **Depends on:** 04 (tab shell), 05 (API) · **Status:** pending

## Goal

The most-watched screen of the demo: run the recon "agent" live, watch the pipeline
trace, review exceptions, decide, export the run log. Single view: controls → trace →
badges → table → drawer. No routing library, no framework.

## New files

| File | Contents |
|---|---|
| `frontend/js/trace.js` | Shared stage renderer (also used by feature 07). Input: SSE events from `api.js`'s existing parser. Renders a vertical step list: stage name, state (pending/running/done), counts ("38 rows loaded", "25 exact matches", "12 exceptions"). Export `createTrace(container)` returning `{onEvent, done, fail}`. This IS the "agentic system, not a chatbot" moment — make states legible (reuse existing spinner/ticker patterns from `render.js` ~line 318-323). |
| `frontend/js/recon.js` | The Reconcile panel: client + period selects → Run button → trace → verification badge row (✅ GSTIN checksums n/N · tax-split n/N · totals n/N) → exceptions table (tabs per bucket + All; columns: invoice_no, vendor, books amt, 2B amt, delta with Indian grouping from `format.js`, status chip) → row click opens detail drawer (books row vs 2B row side-by-side, delta highlighted, LLM explanation with `[Computed]`/model tags, Accept / Correct / Reject buttons + note field) → header actions: "Export run log" (link to `/recon/runs/{id}/log`), "Past runs" select. |

## Files to modify

- `frontend/index.html` — replace the Reconcile placeholder with the panel skeleton.
- `frontend/js/main.js` — wire panel init on tab activation.
- `frontend/js/api.js` — add `runRecon(client, period, {onEvent})` reusing the
  existing SSE machinery (`api.js:137-211` pattern — same frame format by design of
  feature 05), plus fetch helpers for runs/exceptions/decision/log.
- `frontend/css/components.css` — table, drawer, badge styles from existing tokens only.

## Design constraints (from research — these are pitch features, keep them exact)

- **Exceptions-only queue:** matched rows are a count, not a table. The reviewer sees
  only what needs judgment.
- **Verification badges are deterministic facts**, phrased as such ("38/40 GSTINs pass
  checksum"), never a percentage-confidence vibe.
- Every decision requires explicit action; decisions render immediately (optimistic
  update, reconcile with server response).
- Indian digit grouping everywhere amounts appear (`format.js` `normaliseNumbers…`).
- Keyboard/tab accessible; status uses color + icon + word (existing convention).

## Steps

1. `trace.js` + a tiny `trace.test.js` (state transitions on synthetic events). Commit.
2. Panel skeleton + Run wiring + live trace against the real backend. Commit.
3. Badges + exceptions table + bucket tabs. Commit.
4. Detail drawer + decision actions. Commit.
5. Export link + past-runs select. Commit.
6. `node --test frontend/js/` green; `test_api_contract.py` green (any new snake_case
   field you read must exist on feature 05's Pydantic response models);
   `test_frontend_assets.py` green. Full pytest.
7. Manual run of the full demo beat: run → trace → open exception → accept → reject
   another → export log → re-run and switch between runs.

## Cut-lines (if behind)

Drop "Correct" (keep accept/reject) → drop the drawer (explanation inline in an
expandable row). Record cuts in Handoff notes.

## Acceptance criteria

- Full recon demo beat clickable end-to-end with real dataset.
- All JS + Python tests green.

## Handoff notes

_(fill at session end)_
