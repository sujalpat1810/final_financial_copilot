# Feature 04 — Frontend reskin: CA branding, client selector, tab navigation

**Branch:** `feat/frontend-reskin` · **Depends on:** 02 · **Status:** done

## Goal

Turn the annual-report Q&A UI into the CA platform shell: client-aware Ask view +
tab navigation (Ask / Reconcile / Notices) with placeholder panels for features 06/07.
The domain coupling is concentrated — this is mostly copy changes plus one new control.

**Reuse untouched:** `api.js` (SSE client), `viewer.js` (PDF citation viewer),
`confidence.js`, `render.js` markdown/table renderer, `format.js` Indian grouping,
CSS token system, icon sprite. Do not restyle these.

## Files to modify

| File | Change |
|---|---|
| `frontend/index.html` | Product name/welcome copy (~line 96-152) → CA platform ("Practice Copilot" or similar — pick one, note in Handoff). Tab nav (Ask / Reconcile / Notices) in the header region; two hidden placeholder panels. Upload form: add Client (select), Doc type (select: invoice/notice/financials/statute/other), Act version (select, optional) fields. |
| `frontend/js/main.js` | `SEEDS` (~line 163-177) → CA seed questions (below). Doc list rendering (~line 114-140): show client + doc_type instead of `SA · CO` basis counts (keep the no-PDF warning). Upload wiring for new fields (~line 385-448). Client filter dropdown for Ask view, sent as `client` in `QueryRequest` via `api.js`. Simple tab switcher (show/hide panels, one active class — no router). |
| `frontend/js/citations.js` | Chip triplet `p-ent`/`p-fy`/`p-bas` (~line 41-45) → `p-client`/`p-fy`/`p-doctype` (keep basis chip logic only where basis exists). |
| `frontend/js/format.js` | `basisLabel`/`basisShort` (~line 113-124): generalize label helper for doc_type; keep Indian grouping untouched. |
| `frontend/js/api.js` | Only: pass-through of new query fields. No structural change. |
| `frontend/css/*` | Tab styles using existing tokens. No palette changes (contrast is measured + tested). |

## Seed questions (Ask view chips)

1. "What are the conditions for claiming input tax credit under section 16?"
2. "What was Mehta Textiles' revenue in FY2024-25?"
3. "What does the scrutiny notice for Mehta Textiles allege?"
4. "How should a taxpayer respond to an ASMT-10 notice?"
5. "What was Gupta Traders' turnover last year?"  ← the rehearsed ABSTENTION demo

## Steps

1. Tab shell + placeholders; commit.
2. Copy/branding + seeds; commit.
3. Client/doc_type upload fields + doc-list rendering; commit.
4. Ask-view client filter + chip changes; commit.
5. Tests: update `frontend/js/*.test.js` where labels changed; run `node --test frontend/js/`.
   `test_api_contract.py` must pass (it regexes frontend snake_case accesses against
   Pydantic models — any new field you read must exist on the models from feature 01).
   `test_frontend_assets.py` must pass (no external requests, contrast, assets exist).
6. Manual: full Ask flow against the ingested corpus — question → cited answer →
   citation click → PDF highlight; abstention seed → abstention card.

## Acceptance criteria

- Tabs switch; Ask view fully functional with client filtering; placeholders present.
- All JS tests + full pytest green.
- No external network requests introduced (test enforces).

## Handoff notes

- Product name: **Practice Copilot**. Tabs dispatch a `viewshown` CustomEvent
  (detail.view = ask|recon|notices) — recon.js/notice.js should lazy-init on it.
- Panel roots: `#reconRoot` inside `#view-recon`, `#noticeRoot` inside `#view-notices`.
- Chip segments now p-client / p-doctype / p-fy / (p-bas for financials+legacy) / p-pg.
- Client filter: `#clientFilter` select, state.clientFilter, passed as `client` on
  query + queryStream. Options derived from corpus.
- `.view[hidden]{display:none !important}` is load-bearing (row is flex).
- format.js gained `docTypeLabel()`.
