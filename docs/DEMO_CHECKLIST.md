# Pre-demo runbook

Run this top to bottom before every presentation. Total time ~10 minutes.
The full narration script is `docs/DEMO_SCRIPT.md`.

## 1. Reset and verify (T-30 min)

```powershell
.venv\Scripts\activate
python -m scripts.reset_demo          # must end "Clean state" with exit 0
```

The reset wipes runs/decisions/replies and re-seeds; it does NOT touch the
ingested corpus. If any check fails, fix it before going further — especially
`generation provider configured`: a missing key does not error, it silently
turns every answer extractive, which is the worst way to find out on stage.

## 2. Start the server and warm it (T-25 min)

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Wait for `Practice Copilot ready.` (~30 s — model load). Open
http://127.0.0.1:8000/app and ask one throwaway question so the first real
answer of the demo is not the slowest.

**Provider sanity:** the answer card must say nothing about being verbatim
passages. If it reads "No generated answer is available…", the provider is
down or the model id is stale — check `GROQ_MODEL` against
https://console.groq.com/docs/models (this exact failure happened in build
week: `llama-3.3-70b-versatile` started 404ing mid-day and every answer
silently went extractive; the fix was one .env line).

## 3. Smoke the exact demo path (T-20 min)

```powershell
python -m scripts.smoke_ui --shots demo_shots/
```

Covers: cited answer → abstention (named refusal, no citations) → citation
opens the PDF → recon run end-to-end (badges, exceptions, drawer, log
download) → notice analysis (amount ₹38,904, skeleton sections, approval gate
present). Exit 0 or do not present. Screenshots land in `demo_shots/` for a
last visual once-over.

## 4. Rehearsed fallbacks

| If this fails live | Do this |
|---|---|
| Generation down mid-demo | Answers degrade to labelled verbatim passages automatically — narrate it as the honesty feature it is, then continue; retrieval, citations, recon and the gate all still work |
| Notice draft stumbles | `data/ca_dataset/fallback_reply.md` is a committed known-good draft from this exact pipeline — open it and walk the skeleton |
| Anything else | The fallback screen recording (see below) |

**Record the fallback video during rehearsal:** one clean full run, screen
captured. Keep it out of git; note its path here → `________________`

## 5. Failure drills (rehearse once, day before)

1. **Abstention** — ask the Gupta Traders seed. Expect: refusal naming the
   company, zero citations, faster than an answered question.
2. **Provider loss** — temporarily set `GROQ_API_KEY=invalid` in `.env`,
   restart, ask a question. Expect: extractive answer with source labels, no
   stack trace anywhere. Restore the key after.
3. **Double-click Run** — mash the recon Run button. Expect: separate
   immutable runs, no corruption, past-runs dropdown grows.
4. **Cold start** — reset, start the server, load the page during warm-up.
   Expect: "Starting up…" that recovers by itself; never a dead page.

## 6. The five-beat click path (~15 min)

1. **Ask** — statute seed → cited answer → click citation → PDF highlight.
2. **Abstain** — Gupta Traders seed → named refusal.
3. **Compare** — tax-audit seed (toggle auto-sets) → 1961 s.44AB vs 2025 s.63.
4. **Reconcile** — Mehta / Dec 2025 → live trace → badges (point at 38/39 —
   the checksum catching a planted bad GSTIN on screen) → open the amount
   mismatch → accept with a note → reject another → download the run log:
   "this is your working-paper trail."
5. **Notices** — analyze the ASMT-10 → extracted ₹38,904 (same figure the
   recon quantified — one story, not two features) → draft with provision
   chips → open s.75(4)'s source → **Approve**: "nothing leaves this screen
   without a CA's sign-off."

Then the ROI slide and roadmap from `docs/BLUEPRINT.md`.
