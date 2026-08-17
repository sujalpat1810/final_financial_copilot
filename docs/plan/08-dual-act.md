# Feature 08 — Dual-Act temporal beat (1961 vs 2025 Income-tax Act)

**Branch:** `feat/dual-act` · **Depends on:** 03, 04 · **Status:** done

## Goal

The differentiator no competitor demos: the Income-tax Act, 2025 replaces the 1961 Act
from 1 Apr 2026 (819 → 536 sections, nearly all renumbered). Returns for AY 2026-27
still run on 1961 numbering, so CAs work BOTH numberings for ~12-18 months. Show one
question answered under each Act, side by side, with the section mapping.

**The backend is already done** by features 01+02: statute extracts carry
`act_version` metadata ("1961"/"2025") and `QueryRequest` filters on it. This feature
is a UI composition plus the `dual_act` prompt (feature 03).

## Files to modify

| File | Change |
|---|---|
| `frontend/index.html` | On the Ask view: a "Compare 1961 ↔ 2025" toggle (visible only for income-tax questions is over-engineering — just a toggle next to the composer). |
| `frontend/js/main.js` | When toggle is ON: fire TWO queries via the existing `api.js` (sequential, not parallel — free-tier rate limits), one with `act_version="1961"`, one with `"2025"`, `task="dual_act"`. Render side-by-side two-column layout (grid, stacks on narrow), each column headed by the Act name + section number pulled from the answer, each with its own citations/confidence per existing card markup. Below both: the mapping line ("s.40A(3) (1961) ↔ s.NN (2025)") if present in either answer. |
| `frontend/css/layout.css` | Two-column compare grid using existing tokens. |
| Seed | Add one compare-mode seed question: "Is disallowance of cash expenditure above the limit applicable, and under which section?" |

## Steps

1. Toggle + dual-query wiring (sequential; reuse pending-card per column). Commit.
2. Side-by-side layout + mapping line. Commit.
3. JS tests for the render path with canned responses; `test_api_contract.py`; full
   pytest untouched-green. Commit.
4. Manual: run the compare seed both ways; verify each column cites only its own Act's
   PDF and the viewer opens the correct extract.

## Cut-line

If behind: drop the toggle; rehearse the beat as two narrated sequential questions
("now watch me ask the same thing for the new Act") — the metadata filtering already
makes that work with zero UI change.

## Stretch (ONLY if this feature finishes early — else skip entirely)

Client briefing: `GET /clients/{client_id}/briefing` in a small `app/routes_clients.py`
— composes `structured.py` facts (deadlines, open exceptions, notice/reply status,
last run stats) + ONE client-filtered RAG summary call (`qa_client_docs`) → card on
the Ask view under a "Brief me on {client}" button. If attempted, record in Handoff.

## Acceptance criteria

- Compare toggle produces two correctly-filtered, correctly-cited answers side by side.
- All tests green.

## Handoff notes

- Toggle #compareActs in the composer; seed "When is a tax audit of business
  accounts required?" auto-enables it. Sequential queries via api.query with
  actVersion 1961 then 2025; per-column cards rendered by renderResponse.
- Stretch (client briefing) NOT attempted — time went to browser verification
  of features 06/07. It remains the documented cut.
- Browser-verified: 44AB vs 63 with chips per column.
