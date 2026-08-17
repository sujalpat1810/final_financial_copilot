# Feature 10 — Consulting blueprint + demo script documents

**Branch:** `feat/blueprint-docs` · **Depends on:** none (docs only; can run any time after Session 0; final polish after 09) · **Status:** pending

## Goal

Write `docs/BLUEPRINT.md` (the 28-section consulting blueprint the user will use with
the 3 CA firms) and `docs/DEMO_SCRIPT.md` (exact click-path + narration). **Everything
needed is embedded below — do NOT re-research.** Where a fact is marked ⚠️ attribute
it; ❌ items must not appear on slides.

## BLUEPRINT.md — 28 sections (user-specified), content sources

1-3 (exec summary / pain points / ranked opportunities), 4 (demo use cases),
5-6 (dataset), 7-9 (RAG/agent/tool architecture), 10 (RAG vs DB vs tools),
11-12 (architecture comparison + recommendation), 13-15 (diagrams/flows),
16 (UI), 17-18 (storyline/script — cross-ref DEMO_SCRIPT.md), 19 (ROI),
20 (security), 21 (stack), 22 (MVP plan — cross-ref plan files), 23 (roadmap),
24 (what NOT to build), 25-26 (partner Q&A), 27 (integrations), 28 (commercial).

### Embedded research facts

**Pain points / urgency (section 2-3):**
- GSTR-3B hard-locking: outward liability (Tables 3.1/3.2) non-editable since Jul 2025
  ✅; ITC Table-4 locking from GSTR-2B/IMS targeted ~Jul 2026 ⚠️ "industry-indicated,
  notification unconfirmed". IMS: inaction = deemed acceptance ⚠️.
- Monthly GST workflow: 11th suppliers' GSTR-1 → 14th 2B generated → 15th match vs
  purchase register → 16th IMS actions → 20th GSTR-3B ⚠️ (caclubindia practitioner).
- Mismatch taxonomy: in-2B-not-books / in-books-not-2B / amount / GSTIN+vendor-name;
  long tail: duplicates, B2BA amendments, CDNs, ISD, RCM.
- TDS: 26AS-only rule (credit claimable per 26AS, not AIS ✅); causes: timing,
  wrong PAN/TAN, deductor misreporting; 2-5 h/engagement ⚠️ (CORAA, vendor).
- Income-tax Act 2025 replaces 1961 Act from 1 Apr 2026: 819→536 sections, renumbered;
  AY 2026-27 filings still on 1961 numbering → dual-numbering for ~12-18 months ✅
  (CBDT transition FAQ: incometaxindia.gov.in/documents/81799/11848482/FAQs-on-Interplay-and-Transition.pdf).
- Form 3CD heaviest clauses: 44 (GST supply-basis vs expenditure-basis bridge),
  34 (TDS books↔TRACES), 21 (disallowables ledger scrutiny), 22/s.43B(h) (MSME ageing) ⚠️.
- Notices: ASMT-10 (s.61+Rule 99) → ASMT-11 within 30 days → ASMT-12 or DRC-01
  (s.73/74/74A) → DRC-06 reply → DRC-07 order → APL-01 appeal ✅. Reply structure:
  Header/Facts/rebuttal/Prayer + s.75(4) hearing right ⚠️.
- CBDT FY 2026-27 compulsory scrutiny includes "recurring additions in earlier years"
  → justifies previous-year-comparison work ✅.
- ❌ Do NOT quote: aggregate notice volumes; "SEBI/MCA mandated AI disclosure in
  audits" (found to be false).

**Competitors (sections 3, 24-26):**
- ClearTax Clear Finance Cloud: reco+filing at scale; entry ~₹499/mo, enterprise ₹45k+;
  no legal drafting. TaxCloudIndia shutting Mar 2026 ⚠️ (displacement opportunity).
- Suvit → **Vyapar TaxOne** (acquired/rebranded ~Dec 2025 ⚠️ — get this right in the
  room): WhatsApp collection, OCR→Tally, ₹10k/yr.
- Desktop suites (Computax/Genius/Winman ~₹9,850/KDK ₹6,300/Saral ₹6,090): forms
  engines, zero reasoning. **Price band anchor: ₹6,000-30,000/firm/yr.**
- **CORAA (coraa.ai): the direct competitor** — ledger scrutiny, GST/TDS reco,
  vouching OCR, 60 working papers, 3CD/CARO outputs, TallyPrime connector,
  ₹2-3k/entity/yr, AWS Mumbai, ISO 27001, "deterministic AI, NFRA-defensible"
  framing ⚠️ (their site). Do NOT compete on working-paper breadth.
- Big-4: Deloitte Omnia agentic (Jun 2026 ✅), **Deloitte India Tax Pragya** (Dec 2025,
  1.2M+ cases, enterprise pricing ✅), **EY India AI Tax Hub** (Feb 2026, aimed at
  CFOs ✅). Thomson Reuters CoCounsel: zero Indian content ✅ — cleanest white-space
  statement.
- ICAI CA GPT (free, 5,000 listed-company reports): research tool, not practice
  workflow; proves ICAI normalized AI ✅.
- **White space we occupy:** (A) mismatch → *drafted reply with citations* at
  small-firm price; (B) dual-Act temporal grounding.

**Architecture comparison (section 11 — answer the swarm question explicitly):**
| Arch | Verdict |
|---|---|
| A. RAG+LLM | Necessary base, insufficient for workflows |
| B. RAG+tools | Good, but no workflow state/HITL |
| C. **Deterministic pipelines + constrained LLM steps + ONE bounded loop (chosen)** | Predictable, testable, cheap, auditable |
| D. Supervisor+specialists | Unjustified: steps are predictable; cost/debug overhead |
| E. Swarm | **Rejected.** Anthropic: multi-agent ≈ 15× tokens, for parallelizable read-heavy research only; Cognition: conflicting writes → incompatible outputs; LangChain: recon is write-heavy = worst fit; Berkeley MAST (arXiv:2503.13657): ~75% of multi-agent failures are silent "gray" errors — worst possible class for audit |
Sell line: "Anthropic's and OpenAI's own guidance says workflow when steps are
predictable. Our one dynamic loop is retrieval, where step count isn't knowable."
Framework: custom FastAPI (LangChain's own advice: full control, no hidden prompts;
AutoGen retired 2026 = framework risk is real). Revisit LangGraph only >~8 stateful
nodes or multi-day resumable runs.

**Trust/HITL (sections 16, 20):**
- Incidents (verify citations before presenting): ITAT Bengaluru Dec 2024 — recalled
  own order, 4 fake judgments via ChatGPT, ₹669-cr dispute ⚠️ (LiveLaw); SC *Pooja
  Ramesh Singh v. J&K Bank* 2026 INSC 668 "no decision in the eyes of the law" ⚠️;
  Deloitte Australia AUD ~$439k partial refund, fabricated citations ✅; Stanford
  RegLab: Lexis+ AI >17%, Westlaw >34% hallucination, weakest on time-specific
  questions ✅ (reglab.stanford.edu).
- Design: NO confidence % (research: increases overreliance, reduces expert accuracy);
  deterministic verification badges; field provenance with page deep-links;
  source-class labels; exceptions-only approval queues; append-only run log =
  working-paper trail (ICAI due-diligence evidence); abstention (RCT: RAG-arm
  hallucination ≈ no-AI baseline — Schwarcz et al.).
- Demo vs production security table: demo = local, synthetic data, no auth. Production
  = auth+RBAC, per-client isolation, encryption at rest, DPDP (Rules notified 13 Nov
  2025; substantive obligations 13 May 2027 ✅ → "DPDP-ready by design"), India
  hosting (trust requirement; CORAA sets the bar), prompt-injection hygiene (retrieved
  text is data not instructions), citation post-validation, audit trail retention.

**ROI model (section 19) — three tiers, label everything:**
1. Conservative: **10-30% time saving** — Choi/Monahan/Schwarcz RCT, blind-graded
   (Minnesota Law Rev 2024) ✅.
2. Optimistic: **38-115% productivity** — Schwarcz et al. RAG arm ✅ (label: law
   students, RAG tool, not Indian CAs).
3. Rupee bridge: **ICAI Minimum Recommended Scale of Fees calculator**
   (tmdicai.org/fees-calculator.php — explicitly time-derived ✅): run for the firm's
   city class + 3-4 real assignment types. Labour at articled-assistant rates
   (ICAI minimum stipend ₹4-6k/mo ✅; market ₹5-15k metro ⚠️) — harder to challenge.
- Corroboration (labelled self-reported): TR Future of Professionals 2025 ~5 h/wk ≈
  $19k/professional/yr ✅-as-survey; 30-client practice ≈ 2,520 GST cycles/yr ×2h ≈
  3 FTE ⚠️ (practitioner estimate, caclubindia).
- Worked example per firm size (small 1-3 partners / medium / large): hours × rate ×
  the conservative band; show the arithmetic; state assumptions in a box.
- Honesty asset: vision-LLM line-item extraction is 57-63% vs 82-87% purpose-built
  (BusinessWareTech benchmark) — say it, then explain why the demo uses structured
  data + arithmetic validation. Debunking "99.8%" vendor claims buys credibility.

**Partner Q&A (sections 25-26) — prep at minimum:**
"How is this different from CORAA/ClearTax?" (reasoning + drafting + dual-Act vs their
reco/working-paper breadth; complement not replacement) · "What about hallucinations?"
(incidents → abstention demo → citation post-validation → RCT baseline) · "Where does
our data go?" (local/India hosting path, DPDP timeline, provider data terms, paid-tier
no-training options) · "What does ICAI say?" (no AI-specific standard yet; Code of
Ethics duties unchanged; run log = due-diligence evidence) · "Tally?" (roadmap phase 2:
Tally/Zoho/portal-JSON ingest; Account Aggregator for bank data ✅ Sahamati/RBI SRO) ·
"Price?" (₹6-30k/firm/yr band positioning) · "What if the model improves?" (provider
abstraction — Claude drop-in is the live proof).

**Roadmap (section 23):** P1 demo (this build) → P2 pilot: real data, auth, client
isolation, Tally/portal-JSON ingest, SQLite→Postgres, per-firm calibration
(`scripts/calibrate.py` harness exists) → P3 production: Zoho/Tally connectors, email
drafting with send, DMS, DPDP compliance package, India hosting → P4 selective agents
only where step-count is unpredictable (research agent over full statute corpus +
case law), never for arithmetic.

**Commercial (section 28):** productize as "one workflow deep" (GST notice lifecycle)
before broad platform; per-firm annual license in the anchored band; the dual-Act
window (~through FY 2027-28 filings) is a time-limited wedge — move fast.

## DEMO_SCRIPT.md

Beats with exact click-path, narration lines, timings (~15 min), from 00-OVERVIEW.md
storyline. Include: pre-demo checklist reference (docs/DEMO_CHECKLIST.md from feature
09), the rehearsed abstention question, fallback plans per beat (canned draft
`?canned=1`, fallback video), and the "verify before presenting" list: Jul 2026 ITC
hard-locking notification status; ITAT/SC citations; ICAI Code of Ethics 13th Ed. text;
Zoho Practice pricing; TaxCloudIndia shutdown.

## Steps

1. Draft BLUEPRINT.md (sections 1-14) — commit. 2. Sections 15-28 — commit.
3. DEMO_SCRIPT.md — commit. 4. Cross-check every ⚠️/✅ label survived into the text;
no ❌ item present. Merge.

## Handoff notes

_(fill at session end)_
