# Feature 03 — Prompt registry + Claude provider + client entity gate

**Branch:** `feat/prompts-claude` · **Depends on:** 02 · **Status:** done

## Goal

One prompt per task instead of one global prompt; Claude as a third generation
provider behind the existing abstraction; the abstention gate keyed to the demo's
client list instead of the old 120-company gazetteer.

## Files to modify

| File | Change |
|---|---|
| `app/generation.py` | (a) Replace single `_SYSTEM_PROMPT` (~line 58-106) with a registry `PROMPTS: dict[str, str]` — keys `qa_statute`, `qa_client_docs`, `recon_explain`, `notice_reply`, `dual_act`, `default`. `answer()`/`stream_answer()` gain `task: str = "default"`. (b) Add `_call_anthropic` / `_stream_anthropic` beside the Gemini/Groq functions (~line 206-252), register in `_CALL`/`_STREAM` dicts. Reuse retry/quota/extractive-fallback scaffolding untouched. |
| `app/config.py` | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` env fields (default model: consult the claude-api skill at implementation time for the current recommended mid-tier id). Provider resolution (~line 126-148): explicit `GENERATION_PROVIDER=claude` honored; fail-loud in `validate()` if provider=claude with no key (mirrors existing behavior ~line 208-222). |
| `app/entities.py` | Replace `_KNOWN_COMPANIES` gazetteer (~line 75-124) with a small demo list: the 2 client names + their aliases ("Mehta Textiles", "Sharma Electronics") + a handful of NOT-indexed companies for the abstention demo (e.g. "Gupta Traders", plus real-sounding ones like Reliance/Infosys so the gate demo works with natural questions). KEEP: longest-first span consumption (`find_mentions` ~line 160-195), corporate-suffix rule (~line 129-141), precision-over-recall design. |
| `.env` / `.env.example` | Document the three new vars. |

## Prompt requirements (all prompts)

Carry over the existing prompt's discipline (see old `_SYSTEM_PROMPT` for the pattern):
cite `[Page N]` for every factual claim; never invent sources; exact refusal string
when context lacks the answer; figures must carry qualifier context.

New, all tasks: **source-class labels** — instruct the model to tag statements
`[Statute]` / `[Client document]` / `[Computed]` where derivable from the labelled
source blocks (`_source_label` already emits entity/client metadata per source; extend
it to include doc_type so the model can tag correctly). Anything from model knowledge
must be tagged `[General]` and kept to a minimum.

Task-specific:
- `qa_statute`: quote provision language precisely; name section + sub-section; if
  multiple provisions apply, list each.
- `qa_client_docs`: figures must carry client + FY + page.
- `recon_explain`: given one exception (both rows + delta as JSON), explain the likely
  cause in ≤3 sentences using the 4-bucket taxonomy language a CA uses (e.g. "supplier
  has not filed GSTR-1, ITC not yet available in 2B"), then a one-line recommended
  action. Output JSON: `{cause, recommended_action, bucket_confirmed}`.
- `notice_reply`: fixed skeleton — Legal Header / Statement of Facts / Point-wise
  rebuttal (each point citing retrieved provisions) / Prayer for Relief / request for
  personal hearing under s.75(4). Formal Indian legal-correspondence register.
- `dual_act`: answer strictly from the provided act_version's extract; state the act
  name + section number prominently; note the cross-Act mapped section number if the
  mapping table is in context.

## Steps

1. Prompt registry + `task` param threading (callers in `main.py` pass task —
   `qa_statute` vs `qa_client_docs` can be chosen by whether retrieval was
   statute-filtered; `default` otherwise). Commit.
2. Claude provider + config + validate. Add `anthropic` to `requirements.txt`
   (+ lockfile note). **Load the claude-api skill before writing this code** for
   current SDK/model ids. Commit.
3. Tests: extend `tests/test_generation.py` using its existing mock pattern — registry
   returns distinct prompts per task; anthropic call/stream mocked; VENDOR_WORDS
   blocklist still passes (add "anthropic"/"claude" to it — vendor neutrality of
   responses must hold). Commit.
4. Entity gate swap + `tests/test_entity_gate.py` update (gate fires on "Gupta
   Traders", silent on "Mehta Textiles"). Commit.
5. Full pytest green. Manual: one question per provider (`GENERATION_PROVIDER=groq`,
   then `=gemini`; claude only if key present — otherwise assert startup refusal works).

## Acceptance criteria

- `GENERATION_PROVIDER=claude` without key → startup refusal with actionable message.
- With Groq: statute question returns cited, source-class-labelled answer.
- Abstention demo works: un-indexed company question → INSUFFICIENT before scores.
- Full pytest green.

## Handoff notes

- Task keys: default / qa_statute / qa_client_docs / recon_explain / notice_reply /
  dual_act. `main._task_for(req)` picks by metadata filters (act_version→dual_act,
  doc_type=statute→qa_statute, client→qa_client_docs).
- Claude provider: `GENERATION_PROVIDER=claude` + ANTHROPIC_API_KEY; default model
  claude-opus-5; refusal stop_reason raises → extractive fallback. UNTESTED against
  the live API (no key yet) — mocked tests only. When a key arrives, run one manual
  call; consider server-side `fallbacks: "default"` beta then.
- recon_explain returns bare JSON {cause, recommended_action} — feature 05 parses it.
- Source labels now: client|doc_type-class|FY|basis|page. `[General]` is the
  model-knowledge tag (not [Model knowledge]).
- Entity gate INDEXED test fixture is the demo set; calibration probes checked
  against {Infosys, TCS} (their own corpus).
- 338 tests green.
