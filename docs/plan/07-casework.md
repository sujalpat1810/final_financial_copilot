# Feature 07 — Notice → Reply workflow (ASMT-10 → drafted ASMT-11 + approval gate)

**Branch:** `feat/casework` · **Depends on:** 03 (prompts), 04 (tab shell) · **Status:** pending

## Goal

The emotional centerpiece for a CA audience: upload/select a scrutiny notice, watch
the system extract the discrepancy, retrieve the governing provisions, and draft a
structured reply — then STOP at a human approval gate. Positioning: *a draft for CA
review, never an autonomous output* (VLAIR: humans still win drafting — say so).

## New files

| File | Contents |
|---|---|
| `app/casework.py` | `extract_discrepancy(doc_id)`: pull the notice's chunk text from the chunk store (already ingested in feature 02) → ONE constrained LLM call → JSON `{notice_type, period, alleged_discrepancy, amount, sections_cited, reply_due}`; validate against Pydantic model; deterministic fallback: regex the amount/period if LLM unavailable. `draft_reply(discrepancy)`: RAG retrieve with `filters={"doc_type": "statute"}` (top provisions for s.61/Rule 99, s.16, s.73/74/75 — query composed from discrepancy fields) → `notice_reply` prompt (fixed skeleton: Legal Header / Statement of Facts / Point-wise rebuttal, each point citing sources / Prayer for Relief / s.75(4) personal-hearing request) → returns draft + sources. Persist draft in `notice_replies` table `status='draft'`. `decide(reply_id, action, note, actor)`: approval-gate write (approve/request_changes) via `structured.py`'s `approvals`. "Send" is a status change only — NO email integration. |
| `app/routes_casework.py` | APIRouter: `POST /notices/{doc_id}/analyze` (SSE stages: extract → retrieve → draft → done, same frame contract); `GET /notices` (documents with doc_type=notice + their latest reply status); `GET /notices/replies/{id}`; `POST /notices/replies/{id}/decision`. Mount in `main.py`. |
| `frontend/js/notice.js` | Notices panel: notice list → Analyze button → trace (`trace.js` from feature 06) → discrepancy card (fields + [Client document] tag) → draft reply rendered by `render.js` markdown with citation chips linking into `viewer.js` (statute PDFs open at cited pages) → gate: **Approve** / **Request changes** buttons + note; approved state shows "Approved by CA — ready to send" (and nothing more — no send). |

## Files to modify

- `app/structured.py` — add table:
  `notice_replies (reply_id INTEGER PK, notice_doc_id TEXT, discrepancy_json TEXT,
  draft_md TEXT, sources_json TEXT, model TEXT, prompt_version TEXT,
  status TEXT DEFAULT 'draft', created_at TEXT)`.
- `frontend/index.html`, `main.js` — replace Notices placeholder, wire panel.
- `app/runlog.py` reuse — analyze runs also log JSONL (stage events + model + prompt_version).

## Risk mitigation (this is the one long-form LLM output judged by domain experts)

1. Fixed skeleton lives in the prompt; the model fills sections, never invents structure.
2. Extraction and drafting are SEPARATE calls — each simple, each validated.
3. Retrieval pinned by metadata filter to statute chunks so citations can only point
   at provisions.
4. **Pre-generate one known-good draft during this session and commit it** under
   `data/ca_dataset/fallback_reply.md` — the rehearsed fallback if live generation
   stumbles on stage (quota, latency). The UI gets a dev-only query param
   (`?canned=1`) to load it.
5. Cut-line if behind: ship "discrepancy extracted + relevant provisions listed with
   citations" without the full draft.

## Steps

1. `notice_replies` DDL + `casework.extract_discrepancy` + tests (stub LLM per
   `test_query_endpoint.py` pattern; regex fallback tested without LLM). Commit.
2. `draft_reply` + prompt wiring + tests (stubbed generation returns skeleton —
   assert structure preserved, sources attached, status=draft). Commit.
3. Routes + SSE + endpoint tests. Commit.
4. `notice.js` + panel + citation links into viewer. Commit.
5. Pre-generate + commit the fallback draft. Full pytest + JS tests. Commit.
6. Manual: full beat — analyze → discrepancy → draft with clickable citations →
   approve → status flips. Verify the discrepancy figure matches the recon story
   (dataset was built so ASMT-10 amount ties to planted deltas).

## Acceptance criteria

- Full notice beat clickable end-to-end; approval writes an `approvals` row with actor
  + timestamp; draft immutable after approval (new draft = new row).
- Every rebuttal point in a live draft carries at least one citation chip that opens
  the right statute PDF.
- Full pytest + JS tests green.

## Handoff notes

_(fill at session end)_
