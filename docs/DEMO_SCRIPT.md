# Demo script — Practice Copilot for CA firms

~15 minutes of demo + 5 of discussion. Run `docs/DEMO_CHECKLIST.md` first.
Facts marked ⚠️ below are attributed estimates — say the attribution out loud.
Before presenting, verify the items in the "Verify before you present" list at
the end.

---

## Opening: the trust story (2 min, before touching the product)

> "Before I show you anything, let me tell you why this product is built the
> way it is.
>
> In December 2024, an ITAT bench recalled its own order in a ₹669-crore
> dispute — it had cited four judgments that do not exist, traced to
> unverified ChatGPT output. In 2025, Deloitte Australia partially refunded a
> government engagement worth about AUD 440,000 after its report was found to
> contain fabricated citations. And Stanford's RegLab tested the leading paid
> legal AI tools and found even they gave wrong or hallucinated answers on
> 17–34% of queries — worst on time-specific questions, which is exactly what
> tax is.
>
> So the rule this system is built on: **it never invents a source, and when
> it cannot support an answer, it refuses.** Everything I show you follows
> from that rule. And nothing it drafts leaves the screen without a CA's
> sign-off."

*(Verify the ITAT citation details on LiveLaw before quoting the figure.)*

---

## Beat 1 — Ask: cited answers (3 min)

**Click:** Ask tab → seed *"What are the conditions for claiming input tax
credit under section 16?"* → Ask.

**While it streams:** "The evidence appears first — sources and confidence in
about two seconds, before the prose. You judge the answer by its evidence, not
its fluency."

**When done:** point at the `[Statute][Page 1]` tags — "every statement is
tagged with what KIND of source backs it: statute, client document, or the
model's own knowledge, kept to a minimum."

**Click a citation chip.** The CGST extract opens at the page, passage
highlighted. "Every citation opens the actual page. If the exact text can't be
located confidently, it says so rather than highlighting the wrong paragraph —
pointing a CA at the wrong row of a document is a failure that looks like a
feature."

## Beat 2 — The refusal (1 min)

**Click:** seed *"What was Gupta Traders' turnover last year?"* → Ask.

> "Gupta Traders is not a client here. Watch: it refuses, BY NAME, tells you
> what it does hold, offers zero citations — and notice it refused faster than
> it answers, because it never even attempted to generate. A wrong answer
> about the wrong client is the one failure a tool like this cannot have."

## Beat 3 — Dual-Act compare (2 min)

**Click:** seed *"When is a tax audit of business accounts required?"* (the
compare toggle sets itself) → Ask.

> "You are all living this: returns for AY 2026-27 still run on the 1961 Act,
> while the 2025 Act with its renumbered sections applies from Tax Year
> 2026-27. For the next year-plus, every practice works in two numbering
> systems at once. The same question, answered under each Act, each column
> citing only its own Act's text — s.44AB on the left, s.63 on the right,
> with the mapping stated."

*(The extract itself carries "verify against the enacted text" — if asked,
that disclaimer is deliberate and stays in production.)*

## Beat 4 — The reconciliation agent (4 min)

**Click:** Reconcile tab → Mehta Textiles, December 2025 → Run.

**While the trace runs:** "This is what makes it an agent rather than a
chatbot — and every line of that trace is a real pipeline stage with real
counts, nothing animated for effect. One design rule: **the model never
touches a number.** Matching and arithmetic are deterministic code — same
inputs, same output, every run — which is what makes this defensible as a
working paper. The model's only job is explaining each exception in practice
language."

**When badges appear:** point at *38/39 2B GSTINs pass checksum* — "it just
caught a structurally invalid GSTIN in the government's own data feed, by
checksum, on screen." Then *ITC at risk ₹38,904* — "remember that number."

**Exceptions queue:** "Thirty-one records matched — you never see them. Your
attention goes only where judgment is needed: twelve exceptions across five
buckets." Open an *In books, not 2B* row → read the explanation — supplier
hasn't filed GSTR-1, s.16(2)(aa) condition fails, follow up and don't avail.
"That's the explanation your article would write, in two seconds."

**Decide:** Accept it with a note. Reject another. "Every decision is recorded
— who, what, when."

**Download the run log.** "Every stage, every count, every model call,
timestamped, append-only. This is your working-paper trail — how you evidence
due diligence over AI-assisted work."

## Beat 5 — Notice to reply (3 min)

**Click:** Notices tab → the ASMT-10 → Analyze.

**Discrepancy card:** "It read the notice: ASMT-10, December 2025, alleged
excess ITC of **₹38,904 — the exact exposure the reconciliation just
quantified. This is one workflow, not two features:** the recon found it, the
notice demands an explanation of it, and the reply you're about to see
addresses it. One safeguard worth naming: the amount is read *literally* off
the notice by deterministic code; if the model reads a different number, the
literal one wins."

**The draft:** walk the skeleton — Legal Header, Statement of Facts,
point-wise submissions each citing s.61, s.16, s.73/74, the Prayer, and the
s.75(4) personal-hearing request. Click one provision chip — the statute
opens. "Retrieval was pinned to the statute library, so the draft
*structurally cannot* cite anything that is not a provision."

**The gate:** "And here it stops. This is a draft for your review — there is
no send button anywhere in this product. **Approve** — recorded against the
reviewer, and the draft is now immutable; changes mean a new draft. Your
professional judgment stays exactly where ICAI says it must."

---

## Closing: ROI and roadmap (3 min)

> "What's this worth? I'll only give you numbers that survive scrutiny.
>
> The conservative case: **10–30% time saving** on document-heavy work —
> that's from a blind-graded randomized controlled trial, not a vendor claim.
> The optimistic case, from a second RCT of a retrieval tool like this one:
> **38–115% productivity gains** ⚠️ *(measured on law students, on legal
> tasks — I'm telling you its limits because that's the point of this
> product)*. And that same study found the RAG group hallucinated **no more
> than the no-AI control group** — grounding works, measurably.
>
> In your terms ⚠️ *(practitioner estimate)*: a 30-client practice runs
> roughly 2,500 GST reconciliation cycles a year — at ~2 hours a cycle,
> that's close to three full-time people doing nothing but matching. This
> does the matching deterministically and the explaining automatically; your
> people do the deciding.
>
> Costing it your way: take ICAI's own Minimum Recommended Scale of Fees —
> which ICAI states is derived from average time per assignment — for your
> city class and your actual assignment mix, and apply the 10–30% band. I'll
> happily build that number with your figures rather than mine.
>
> What's next if you want it: pilot on your real data — Tally and portal-JSON
> ingest, per-client isolation, login — hosted in India, designed against the
> DPDP Rules ahead of the May 2027 enforcement date. The full architecture
> and roadmap are in the blueprint document I'll leave with you."

---

## Q&A quick answers

(Full versions in `docs/BLUEPRINT.md` § Partner Q&A.)

- **"How is this different from ClearTax / our reco tool?"** They reconcile
  and file at volume — and stop at a mismatch report. This connects the
  mismatch to the law and to the drafted reply, with verifiable citations,
  at practice scale.
- **"Hallucinations?"** Point back at Beat 2. Abstention is measured behavior,
  not a promise; citations resolve to pages or aren't rendered as citations.
- **"Where does our data go?"** Demo: this laptop, synthetic data. Pilot:
  India-hosted, per-client isolation; the LLM sees retrieved excerpts, and
  the roadmap includes self-hosted models if you want nothing leaving.
- **"What does ICAI require?"** No AI-specific standard yet — the Code of
  Ethics duties are unchanged and personal. That's why the run log exists:
  it's your due-diligence evidence.

## Verify before you present

- ITAT Bengaluru recall details (LiveLaw) and the SC *Pooja Ramesh Singh*
  citation, before quoting either.
- GSTR-3B ITC hard-locking (Table 4) notification status — say
  "industry-indicated" unless a CBIC notification has landed.
- The 1961→2025 mapping (44AB→63) against the enacted text.
- `GROQ_MODEL` still served (see the checklist's provider sanity step).
