# Feature 09 — Demo hardening & rehearsal (FEATURE FREEZE — no new features here)

**Branch:** `feat/demo-hardening` · **Depends on:** 06, 07, 08 · **Status:** done

## Goal

Make the demo un-embarrassable. This session adds NO features — only reset tooling,
smoke coverage, failure drills, and rehearsal. If you find yourself writing a feature,
stop and re-read the cut order in 00-OVERVIEW.md.

## New/modified files

| File | Change |
|---|---|
| `scripts/reset_demo.py` | Wipe `data/ca_demo.db` runtime tables (runs/matches/exceptions/approvals/replies — keep clients/deadlines/documents), delete `data/runs/*.jsonl`, verify chunk store + FAISS index intact (counts match manifest), re-seed. Idempotent. Print a go/no-go checklist. |
| `scripts/smoke_ui.py` | Extend the existing Playwright gate (~257 lines): keep its structure. New checks: (1) statute question → cited answer appears; (2) abstention seed → abstention card; (3) recon run → trace completes → exceptions table non-empty → one accept persists; (4) notice analyze → draft appears (or canned fallback with `?canned=1`). Exit code gates the demo. |
| `docs/DEMO_CHECKLIST.md` | Pre-demo runbook: reset script → server start → warm-up query → smoke script → provider sanity (which provider active, quota state) → fallback video location → the click-path with timings. |

## Failure drills (execute each, verify behavior, note results here)

1. **Mid-stream network kill** during a streamed answer → partial text kept, stream
   ends gracefully (existing semantics) OR blocking fallback fires. Verify no broken UI.
2. **Provider quota exhaustion** (set an invalid key temporarily) → extractive
   fallback with labelled sources; recon explanations fall back to deterministic bucket
   text; notice flow falls back to canned draft. NO stack traces user-visible.
3. **Abstention** → rehearse the Gupta Traders seed.
4. **Cold start** → full reset, fresh server, first question within warm-up window
   (health polling recovers — verify the ~30s model-load experience is acceptable;
   plan to pre-start the server before the audience arrives).
5. **Double-click / re-run spam** on recon Run → runs are separate rows, no corruption.

## Rehearsal

1. Run the exact `docs/DEMO_SCRIPT.md` click-path TWICE end-to-end, timed (~15 min
   target). Fix only breakages, not wishes.
2. Record a full-run screen capture as the catastrophic-fallback video; store path in
   DEMO_CHECKLIST.md (keep OUT of git if large — note location).
3. Full `pytest` + `node --test frontend/js/` + smoke script — all green.

## Acceptance criteria

- `python scripts/reset_demo.py && uvicorn app.main:app` → clean demo state in <2 min.
- Smoke script green from cold start.
- All five drills verified with notes below.
- Both rehearsal runs completed within time.

## Drill results / Handoff notes

- Smoke: 24/24 green from a clean reset (Q&A, refusal, citation→PDF, recon
  end-to-end, notice with gate). Exit code gates the demo.
- Drill provider-loss: invalid GROQ key → labelled extractive answers, no
  traceback, no key text in the response. PASS.
- Drill double-run: two concurrent recon runs → distinct immutable run ids,
  identical deterministic buckets. PASS.
- Drill abstention: covered in smoke (named refusal, 0 citations, 1.5s vs 4.9s).
- Cold start: reset → server → smoke sequence exercised repeatedly. PASS.
- Real bug found by smoke: open source panel overlaying the Notices button
  after a tab switch — tab switches now close the panel (main.js).
- reset_demo must load_dotenv itself (caught live: provider read "none").
- Fallback screen recording NOT made (no display recording in this env) —
  record during your rehearsal, note path in DEMO_CHECKLIST.md.
- Groq daily cap note: a full smoke ≈ 16 LLM calls; several smokes/day fit
  ~1,000/day easily.
