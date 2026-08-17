# Practice Copilot — Implementation Blueprint

**An AI platform demo for Indian CA firms: RAG with verifiable citations,
deterministic agent workflows, human-in-the-loop by construction.**

Prepared August 2026. Sources are cited inline; claims marked ⚠️ are
single-source or practitioner estimates and are labelled as such wherever they
appear. Items in § *Verify before presenting* must be re-checked against
primary sources before any client meeting.

---

## 1. Executive summary

Indian CA firms sit on the most automatable workload in professional services:
high-frequency, document-heavy, deadline-driven compliance work whose rules
are written down. Yet the AI products serving them split into two camps —
volume reconciliation/filing tools that stop at a mismatch report (ClearTax,
IRIS, ExpressGST), and enterprise legal-reasoning tools priced and packaged
for Big-4 clients (Deloitte Tax Pragya, EY AI Tax Hub). Nobody credibly
connects *"invoice INV-4471 is in GSTR-2B but not in the books"* to *"here is
the drafted ASMT-11 paragraph addressing it, citing s.61, Rule 99 and
s.16(2)(aa), with the s.75(4) hearing request"* at a price a 3-partner firm
can pay.

This demo occupies exactly that gap with the smallest system that shows the
largest value: hybrid RAG with page-level citations and measured abstention; a
deterministic GST reconciliation agent whose every number is computed by code
and merely *explained* by the model; a notice-to-drafted-reply workflow that
stops at a mandatory CA approval gate; and a dual-Act answer mode for the
1961→2025 Income-tax Act transition that every Indian practice is living
through right now. The trust story is not a slide — it is the product's
observable behavior: refusals by name, citations that open pages, verification
badges a CA can re-derive by hand, and an append-only run log framed as the
working-paper trail.

**The architectural finding worth stating plainly: a multi-agent "swarm" was
evaluated and rejected**, on the strength of the vendors' own published
guidance and measured evidence (§ 11). What ships instead — deterministic
pipelines with constrained LLM steps — is cheaper, testable, reproducible,
and easier to defend in front of a regulator. That is a selling point, not a
compromise.

## 2. CA firm pain points

Ranked by (frequency × time cost × automatability), from 2025-26 sources:

1. **GSTR-2B ↔ purchase register reconciliation** — monthly, per client, on a
   rigid calendar (suppliers' GSTR-1 by the 11th; 2B generated the 14th;
   matching ~the 15th; IMS actions the 16th; GSTR-3B the 20th ⚠️
   practitioner-documented). Structural urgency: GSTR-3B outward tables have
   been hard-locked (non-editable, auto-populated) since July 2025, and
   hard-locking of the ITC table sourced from GSTR-2B is industry-indicated
   for ~July 2026 ⚠️ — reconciliation stops being "fix it later" and becomes
   "fix it before filing". IMS treats inaction as acceptance.
2. **Tax notices** — ASMT-10 scrutiny (s.61 + Rule 99, 30-day reply in
   ASMT-11), escalating to DRC-01 SCNs (s.73/74/74A); income-tax 143(1),
   142(1), 148. Each reply is research + drafting + evidence assembly.
3. **TDS reconciliation** — books ↔ challans ↔ TRACES returns (24Q/26Q/27Q) ↔
   26AS. Operational rule an agent must encode: TDS credit is claimable on
   Form 26AS only, not AIS; an AIS-only entry means fixing the deductor's
   filing first. 2–5 hrs/engagement ⚠️ (vendor figure, CORAA).
4. **Form 3CD tax audit** — clauses 44 (GST supply-basis vs expenditure-basis
   bridge), 34 (TDS compliance vs TRACES), 21 (disallowables ledger
   scrutiny), 22/s.43B(h) (MSME ageing) are the labour sinks ⚠️
   (practitioner consensus).
5. **The dual-Act transition** — the Income-tax Act 2025 (819→536 sections,
   nearly all renumbered) applies from Tax Year 2026-27, while AY 2026-27
   filings still run on 1961 numbering (CBDT transition FAQ). Every practice
   works two numbering systems for 12–18 months.
6. **Statutory audit** — ledger scrutiny, JE testing, exception reports.
   Deliberately out of demo scope (§ 24).
7. **Client management** — document chasing, briefings, correspondence.
   Pilot-phase scope.

## 3. Top AI opportunities, ranked

| # | Opportunity | Why it wins | Demo? |
|---|---|---|---|
| 1 | GST reco + exception explanation | Monthly × every client; deterministic core; visible ROI | ✅ core |
| 2 | Notice → drafted reply with citations | The unowned white space; high emotional value | ✅ core |
| 3 | Statute/client-doc Q&A with citations | The foundation everything sits on | ✅ core |
| 4 | Dual-Act temporal answering | Live pain; no competitor does time-validity well (Stanford RegLab found even Lexis+/Westlaw weakest on time-specific questions) | ✅ core |
| 5 | Client briefing (facts + doc summary) | Cheap composition of 1+3 | pilot |
| 6 | TDS/26AS reconciliation | Same engine shape as #1 | pilot |
| 7 | 3CD clause 44/34 workings | High value; CORAA competes here | roadmap |
| 8 | Document-collection chasing | Practice-management adjacency | roadmap |

## 4. Recommended demo use cases

The four shipped beats: **cited Q&A + named refusal**, **reconciliation agent
with review queue and run log**, **notice → drafted reply behind the approval
gate**, **dual-Act compare**. One storyline stitches them: the reconciliation
*quantifies* ₹38,904 of ITC at risk, the ASMT-10 *demands an explanation of
that same figure*, and the drafted reply *addresses it* — one workflow, not
four features.

## 5. Minimum dataset

Two fictional clients suffice (Mehta Textiles Pvt Ltd — the active story;
Sharma Electronics Pvt Ltd — proves multi-client isolation). The floor is:
one purchase-register/GSTR-2B pair with all mismatch types planted, one
scrutiny notice whose figure ties to the planted mismatches, one prior-year
financial statement, statute extracts for every provision the demo cites, and
the dual-Act pair. Anything more adds ingestion time, not demonstration value.

## 6. Exact documents created

All generated by one script (`scripts/make_ca_dataset.py`) that **asserts the
expected reconciliation outcome before writing anything**, so the data and the
demo cannot drift apart. Committed under `data/ca_dataset/`:

| Artefact | Contents / plant | Consumed by |
|---|---|---|
| `mehta_purchase_register_2025-12.csv` (40 rows) | 30 clean matches; 1 duplicate; 4 books-only; 2 amount mismatches; 2 GSTIN mismatches; 1 amended-value row | Recon (structured lane) |
| `mehta_gstr2b_2025-12.csv` (39 rows) | Counterparts incl. a B2BA amendment pair, one checksum-invalid GSTIN, one unknown supplier ("Zenith Metals") | Recon |
| 6 invoice PDFs | 2 planted defects: supplier GSTIN failing checksum; line items not summing to the stated total | RAG + doc-verification talking point |
| `asmt10_mehta.pdf` | ASMT-10 quantifying **₹38,904** — computed from the planted rows, so notice and recon can never disagree | RAG + casework |
| `mehta_financials_fy2425.pdf` | 2-page P&L + balance-sheet extract | RAG (client Q&A) |
| CGST extracts (s.16; s.61+Rule 99; s.73/74/75) | Faithful bare-act text, demo-marked | RAG (statute Q&A, reply citations) |
| `itact_1961_s44ab.pdf` / `itact_2025_s63.pdf` | The dual-Act pair with the mapping note and a verify-against-enacted-text caution | Dual-Act beat |
| `seed/clients.json`, `seed/deadlines.json` | 2 clients, 5 deadlines | SQLite seed |
| `fallback_reply.md` | A live-generated known-good draft — the rehearsed fallback | Demo insurance |

**RAG vs DB placement rule:** PDFs (unstructured, citable) are ingested into
RAG; CSVs (structured, computable) never are — they feed the deterministic
engine. A row you might compute over goes in the database; a page you might
cite goes in the index.

## 7. RAG architecture

- **Ingestion:** PDF → pdfplumber (pypdf fallback) → paragraph-boundary
  chunking with one-sentence overlap; tables reserialised with explicit
  markers. Every chunk carries operator-supplied metadata — client, doc_type,
  fiscal year, act_version — never inferred from the document (a misattributed
  figure is the failure the product exists to prevent).
- **The provenance-line mechanism:** each chunk's indexed text is prefixed
  with one natural-language line ("Mehta Textiles Pvt Ltd FY2025-26 invoice,
  page 1."). Measured on the predecessor corpus, this moved the key
  statement page from unranked (vector) / rank 118 (BM25) to the top result —
  it is the single highest-leverage retrieval idea in the system, and it makes
  retrieval client-aware with zero filter plumbing.
- **Retrieval:** hybrid — FAISS cosine + BM25 keyword — union-deduped, then
  cross-encoder reranked. Hybrid is the floor, not an optimisation: benchmark
  work on financial documents finds BM25 beating dense retrieval on exact
  terms (section numbers, GSTINs, invoice numbers), which is most of what tax
  questions contain. Equality filters on client / doc_type / act_version /
  fiscal year pin retrieval where the workflow demands it (notice replies
  retrieve statutes *only* — structurally, not by prompt).
- **Confidence + abstention:** discrete states (high / moderate / low /
  insufficient) on raw reranker logits, plus a categorical entity gate — a
  question naming a client whose documents are not held is refused *before*
  generation, by name. No numeric confidence is ever shown (§ 16).
- **Generation:** provider-abstracted (Groq / Gemini / Claude) behind one
  dispatch table; per-task prompt registry; retry with per-day-quota
  detection; and an extractive fallback — no key, no SDK, or a failed call
  yields labelled verbatim passages, never an error and never fabrication.
- Query rewriting and HyDE were considered and rejected: published results
  show HyDE actively counterproductive for numerical financial QA (it
  hallucinates the numbers you are trying to retrieve).

## 8. Agent architecture

Two "agents", both deterministic pipelines with constrained LLM steps, both
streaming their real stage events to the UI as the agent-activity trace:

- **Reconciliation:** load → verify (GSTIN checksum, tax-split legality —
  pure code) → match (exact key → amount comparison → bounded fuzzy on GSTIN
  edit-distance / vendor-name similarity; B2BA amendments resolved first;
  duplicates flagged before matching) → classify into five buckets → one
  constrained LLM call per exception to *explain* it (JSON-shaped output;
  deterministic per-bucket text on any failure). **The model never touches a
  number.**
- **Casework:** extract the notice's particulars (one constrained call, with
  a regex fallback that reads the amount literally — and overrules the model
  if they disagree) → retrieve provisions (metadata-pinned to statutes) →
  draft against a fixed ASMT-11 skeleton → persist as a draft that only the
  approval gate can advance.

The one *dynamic* loop in the system is retrieval Q&A, where step count is
genuinely unknowable. That is the honest boundary between "workflow" and
"agent", and it matches Anthropic's and OpenAI's published guidance (§ 11).

## 9. Tool architecture

| Tool | Kind | Used by |
|---|---|---|
| Hybrid retriever (filters) | code | Q&A, casework |
| GSTIN checksum / tax-split / line-sum / rate-slab checks | pure code | recon badges, dataset assertions |
| Matching engine | pure code | recon |
| SQLite structured store | code | recon, casework, briefings (pilot) |
| Run log (append-only JSONL) | code | recon, casework |
| LLM generate (per-task prompts) | model | Q&A, explanations, extraction, drafting |
| PDF citation viewer | UI | every citation |

## 10. RAG vs database vs tools

- **RAG holds what you'd cite:** statutes, notices, client documents, prior
  reports. Unstructured, page-addressed, retrieved by meaning and keywords.
- **The database holds what you'd compute or track:** clients, deadlines,
  runs, matches, exceptions, decisions, replies. Structured, queried by key,
  never embedded.
- **Tools do what neither should:** arithmetic, matching, checksums, file
  export. Anything with a right answer is code, not a model.

The demo's sharpest expression of this: the reconciliation CSVs are *not* in
the RAG index at all, and the statute PDFs are *only* there.

## 11. Single agent vs multi-agent vs swarm

| Option | Verdict | Grounds |
|---|---|---|
| A. Plain RAG+LLM | Necessary, insufficient | No workflow state, no HITL |
| B. RAG + tools | Good baseline | Still no review/approval surface |
| C. **Deterministic pipelines + constrained LLM steps + one bounded retrieval loop** | **Chosen** | Predictable, testable, reproducible, cheap; produces auditable artefacts |
| D. Supervisor + specialist agents | Rejected for this scope | The steps are enumerable; a supervisor adds cost and failure modes without adding capability |
| E. Swarm | **Rejected** | See below |

The evidence against the swarm, from the vendors themselves: Anthropic's
guidance is workflows for decomposable tasks and complexity "only when it
demonstrably improves outcomes", with its own multi-agent research system
measured at ~**15× the tokens** of a chat interaction; OpenAI's guide says use
traditional code for deterministic flows and start single-agent; Cognition's
production experience ("Don't Build Multi-Agents") shows parallel agents'
conflicting decisions producing incompatible outputs; LangChain — a framework
vendor with every incentive to say otherwise — restricts multi-agent to
read-heavy, parallelizable work and names write-heavy flows (reconciliation
is one) as the poor fit. The academic anchor: the Berkeley MAST taxonomy
(arXiv:2503.13657) finds the majority of multi-agent failures are **silent
"gray" errors** — the worst possible failure class for audit and tax work.

**Framework choice follows the same logic:** custom FastAPI orchestration,
no agent framework. LangChain's own advice favours "full control over what
reaches the LLM… without hidden prompts"; AutoGen's 2026 retirement into
Microsoft Agent Framework is a live demonstration of framework migration
risk. Revisit LangGraph only if the graph exceeds ~8 stateful nodes or pilots
demand multi-day resumable runs.

## 12. Recommended architecture

As built. One FastAPI service; vanilla-JS frontend with zero external
requests (self-hosted fonts, vendored PDF.js — the page works air-gapped,
which *is* the confidentiality pitch); FAISS+BM25+cross-encoder retrieval;
SQLite for structure; JSONL run logs; three-provider generation.

## 13. System diagram

```
CA (browser) — tabs: Ask / Reconcile / Notices
  │  SSE streams (answers, agent traces)
  ▼
FastAPI
  ├─ RAG: FAISS + BM25 → cross-encoder → confidence gate → provider LLM
  │        filters: client · doc_type · act_version · fiscal_year
  ├─ Recon pipeline:  load → verify → match → classify → explain(LLM) → queue
  ├─ Casework:        extract(LLM) → retrieve(statutes) → draft(LLM) → GATE
  ├─ SQLite: clients · deadlines · runs · matches · exceptions · approvals · replies
  ├─ Run logs: append-only JSONL per run (export = the working paper)
  └─ Providers: Groq | Gemini | Claude   (one dispatch table, one fallback)
```

## 14. Data flow

**Q&A:** question (+filters) → hybrid retrieve → rerank → entity gate +
confidence floor → *abstain (sources shown, no prose)* or generate with
per-task prompt → cited answer; evidence streams ~2 s before prose.
**Recon:** CSVs → deterministic pipeline (stages streamed) → matches +
exceptions persisted → per-exception explanation → review decisions →
approvals rows → exportable log.
**Casework:** notice text → particulars (literal-amount safeguard) →
statute-pinned retrieval → skeleton draft → draft persisted → approve /
request-changes → immutable on approval.

## 15. Agent workflow diagrams

```
RECONCILE                              NOTICE
load inputs (40 + 39 rows)             read the notice (LLM + regex literal)
  → verify: checksums, tax splits        → extracted particulars card
  → resolve B2BA amendments              → retrieve provisions (statutes only)
  → flag duplicates                      → draft vs fixed ASMT-11 skeleton
  → exact → amount → fuzzy match         → DRAFT persisted
  → 5 exception buckets                  → ┌─────────────────────────┐
  → explain each (LLM, bounded)          → │  CA APPROVAL GATE       │
  → exceptions-only review queue         → │  approve → immutable    │
  → accept / correct / reject            → │  request changes → back │
  → append-only run log                  → └─────────────────────────┘
```

## 16. UI / dashboard design

Three tabs, one product idea: **verifiability over confidence.**

- **Ask** — corpus sidebar with client filter; streaming answers whose
  evidence lands first; source-class tags; provenance chips (client · type ·
  FY · page) opening the PDF at the highlighted passage; the dual-Act toggle.
- **Reconcile** — the agent trace (real stages, real counts); deterministic
  verification badges phrased as facts ("38/39 2B GSTINs pass checksum") —
  research on clinical decision support found numeric confidence displays
  *increase* overreliance and *reduce* expert accuracy, so no percentage
  appears anywhere; the exceptions-only queue; the side-by-side drawer with
  differing fields highlighted; decisions; the log export.
- **Notices** — particulars card tagged [Model-read] or [Read literally];
  the draft with provision chips; Approve / Request changes.
- Indian digit grouping throughout (`2,25,712`) — the fastest way for a UI to
  look foreign to a CA is Western grouping.

## 17–18. Demo storyline and script

See `docs/DEMO_SCRIPT.md` (narration, click path, timings, Q&A answers) and
`docs/DEMO_CHECKLIST.md` (reset, warm-up, smoke, failure drills, fallbacks).

## 19. ROI model

Three tiers, in order of evidentiary strength; never lead with a vendor claim.

1. **Conservative — 10–30% time saving.** Choi, Monahan & Schwarcz (Minnesota
   Law Review 2024): first blind-graded RCT of professional AI assistance;
   large, consistent speed gains at that band. This number survives a
   skeptical partner.
2. **Optimistic — 38–115% productivity.** Schwarcz et al. (2025), RCT of a
   RAG legal tool ⚠️ *label in the room: measured on law students, legal
   tasks*. Its second finding matters as much: the RAG arm hallucinated **no
   more than the no-AI control** — the architecture's core claim, measured.
3. **The rupee bridge — ICAI's own numbers.** The ICAI Minimum Recommended
   Scale of Fees is explicitly derived from average time per assignment
   (calculator: tmdicai.org/fees-calculator.php). Take the firm's city class
   and real assignment mix, apply band 1, cost the labour at articled/
   semi-qualified rates (ICAI minimum stipend ₹4–6k/month; market ₹5–15k
   metro ⚠️) — those are who actually do this work, which makes the model
   *harder* to challenge, not easier.

Corroboration, labelled as self-reported: Thomson Reuters 2025 — ~5 hrs/week
≈ $19k/professional/year expected value; a 30-client practice ≈ 2,520 GST
reco cycles/year ≈ ~3 FTE ⚠️ (practitioner estimate, caclubindia).

Worked illustration (state assumptions aloud): 30 clients × 12 months × 2 hrs
= 720 hrs/yr of matching ⚠️. At even 50% reduction on the matching/explaining
half (the deterministic engine does it in seconds), ≈ 360 hrs/yr ≈ ₹1.8–5.4
lakh/yr at ₹500–1,500/hr blended — against software priced in the
₹6,000–30,000/firm/yr band (§ 28). The point is not the precise number; it is
that every input is the firm's own.

**Honesty asset:** vision-LLM line-item extraction benchmarks at 57–63%
accuracy (vs 82–87% for purpose-built processors) — which is *why* this demo
computes over structured data and validates arithmetically instead of trusting
extraction. Saying this unprompted, against the "99.8% accuracy" marketing a
partner has already heard, buys more credibility than any feature.

## 20. Security

| Concern | Demo (honest statement) | Production |
|---|---|---|
| Data location | One laptop, synthetic data | India region (competitive table stakes — CORAA leads with AWS Mumbai + ISO 27001) |
| AuthN/AuthZ | None (local) | SSO, roles (partner/manager/article), per-client ACLs |
| Tenant isolation | Single tenant | Per-firm isolation; client as a hard row/index boundary |
| LLM exposure | Retrieved excerpts only, free-tier terms apply | Paid/no-training terms; self-hosted open-weights option for zero-egress |
| Prompt injection / RAG poisoning | Retrieved text treated as data; source-class labels; abstention | + ingestion provenance controls, content signing |
| Citation integrity | Unresolvable markers render as plain text, never fake links | + post-generation citation validator (regenerate-or-abstain — published recipe reached 94.5% citation support, 1.8% hallucination) |
| Audit trail | Append-only JSONL run logs, exportable | + retention policy, reviewer identity from auth |
| DPDP Act 2023 | Synthetic data only | Rules notified 13 Nov 2025; substantive obligations from ~13 May 2027 — "DPDP-ready by design" while incumbents retrofit. No blanket localisation for general personal data today; India hosting is a trust requirement first |
| ICAI obligations | — | No AI-specific standard yet; Code of Ethics duties (integrity, competence, confidentiality, personal accountability) unchanged — the run log is the due-diligence evidence |

## 21. Technology stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | FastAPI + Python 3.11 | Existing, tested (399 tests); threadpool discipline throughout |
| Retrieval | FAISS + BM25Plus + ms-marco cross-encoder | Local, fast, no per-query cost; hybrid is the measured floor for financial text |
| Embeddings | all-MiniLM-L6-v2 (local) | No document text leaves the machine for indexing |
| LLM | Groq (fast, free tier) + Gemini + **Claude (wired, activates on key)** | Provider abstraction is a demo'd feature: models change under you — Groq retired the previous default model *during build week*, and the fix was one .env line |
| Structure | SQLite (stdlib) | Hundreds of rows; a server DB is an install step with no gain. Pilot: Postgres |
| Frontend | Vanilla ES modules, no build step | Zero external requests; works air-gapped; the confidentiality pitch is inspectable |
| Agent framework | **None** | § 11 |
| PDF | pdfplumber/pypdf in; fpdf2 (dataset) out; PDF.js vendored | — |

## 22. MVP implementation plan

Executed as ten single-session features on one integration branch, each with
tests green before merge (`docs/plan/00-OVERVIEW.md` is the tracker):
01 client dimension + filters → 02 asserted dataset + ingestion → 03 prompt
registry + Claude provider + entity gate → 04 UI reskin + tabs → 05 recon
engine (engine tests pin the generator's expected outcome — the demo's
insurance policy) → 06 recon UI (browser-verified) → 07 casework
(browser-verified) → 08 dual-Act → 09 hardening (reset script, extended
smoke, failure drills) → 10 these documents.

## 23. Production roadmap

- **Phase 1 — this demo.** Synthetic data, local, three beats + compare.
- **Phase 2 — pilot (4–8 weeks).** One friendly firm, real engagements:
  Tally exports + GST portal JSON ingestion; auth + per-client isolation;
  SQLite→Postgres; document delete/re-ingest (requires the Chroma-style
  store); per-firm threshold calibration (`scripts/calibrate.py` harness
  exists); TDS/26AS recon as the second engine; client briefings; measured
  before/after times on 3 workflows — *the pilot's output is India's first
  credible measured ROI number for CA work, which is itself a sellable
  asset.*
- **Phase 3 — production.** Tally/Zoho Books connectors; document management;
  notice-deadline tracking; DPDP compliance package (consent, retention,
  breach flow); India hosting; citation post-validation; optional
  self-hosted LLM tier. Bank-data via Account Aggregator (RBI-recognised
  SRO ecosystem) replaces statement OCR entirely — the hardest extraction
  problem converted into an API.
- **Phase 4 — selective agents.** Only where step-count is genuinely
  unpredictable: a research agent over the full statute + circular + case-law
  corpus with time-validity metadata (`in_force_from/to` per provision — the
  VersionRAG problem). Never for arithmetic.

## 24. What NOT to build

- **A swarm** (§ 11). - **General-purpose OCR extraction** — the line-item
  numbers say no; structured sources and Account Aggregator say you don't
  need to. - **Working-paper breadth** — CORAA ships 60 working papers with a
  Tally connector at ₹2–3k/entity/yr; compete on reasoning + citations +
  dual-Act, not on their moat. - **Autonomous filing or sending** — the
  approval gate is the product. - **A framework migration** — no LangChain/
  LangGraph/CrewAI until the orchestration graph earns it. - **Confidence
  percentages** — measurably harmful (§ 16). - **An email integration in the
  demo** — "send" not existing is a feature with a straight face.

## 25–26. Partner Q&A (with answers)

**"How is this different from ClearTax / IRIS / our reco tool?"** Those
reconcile and file at volume, and stop at a mismatch report. This connects the
mismatch to the governing provision and the drafted reply, with citations that
open pages. Complement, not replacement — Phase 3 ingests their outputs.

**"How is this different from CORAA?"** CORAA is working-paper breadth
(60 papers, 3CD/CARO outputs, Tally connector) — genuinely good at that. This
is legal reasoning with verifiable citations plus the dual-Act transition,
which they do not do. Their own best framing — deterministic, reproducible,
NFRA-defensible — describes this system's recon engine exactly.

**"Deloitte has Tax Pragya; EY has an AI Tax Hub."** Yes — trained on 1.2M+
cases, sold to enterprises and CFOs at enterprise prices. That validates the
category and prices the top end. This is practice-shaped and priced (§ 28).

**"What about hallucinations?"** Show, don't promise: the Gupta Traders
refusal (by name, no citations, faster than an answer); citations resolving to
pages or rendering as plain text; the RCT finding that grounded RAG
hallucinated no more than the no-AI control; and the ITAT/Deloitte-Australia
incidents as the reason the abstention exists.

**"Where does our data go?"** Demo: nowhere — this laptop, synthetic data,
zero external requests in the page itself. Pilot: India-hosted, per-client
isolation, LLM sees retrieved excerpts under paid no-training terms; a
self-hosted-model tier if the answer must be "nothing leaves".

**"What does ICAI say about AI?"** No AI-specific standard yet. The Code of
Ethics duties are unchanged and *personal* — which is why the run log exists:
an exportable, timestamped account of every step is how a member evidences
due diligence over AI-assisted work. ⚠️ Verify the 13th-edition Code text
before quoting it directly.

**"Can it do Tally?"** Pilot scope: Tally exports (XML/Excel) are the
integration that decides adoption; the recon engine already consumes
register-shaped tables.

**"What if the model improves or the vendor changes terms?"** The provider
abstraction is the answer, demonstrated: Claude was wired in before a key
existed, and when Groq retired a model mid-build the fix was one line.

**"What happens when it's wrong?"** It is wrong *visibly*: an exception
explanation is labelled with its model; the CA rejects it with a note; the
rejection is recorded. The system's failure mode is a reviewed mistake, not a
silent one.

## 27. Future integrations

Tally (exports → connector), GST portal JSON (GSTR-2B/1/3B), TRACES/26AS,
Zoho Books/Practice, Account Aggregator (bank data, consent-based), email
drafting (behind the same gate), DMS, e-filing utilities.

## 28. Commercial opportunity

- **Price band:** Indian small-firm software is ₹6,000–30,000/firm/yr
  (Winman/KDK/Saral anchor; CORAA at ₹2–3k/entity/yr; Karbon's *cheapest*
  Western tier ≈ ₹90,000/user/yr — that gap is the market). Per-firm annual
  licence, tiered by clients, in-band.
- **Wedge:** one workflow deep — the GST notice lifecycle (reco → exposure →
  notice → reply) — before any broad-platform ambition.
- **Timing:** the dual-Act window is a time-limited differentiator
  (~through FY 2027-28 filings); the ITC hard-locking timeline ⚠️ makes
  pre-filing reconciliation urgent *now*; TaxCloudIndia's announced shutdown
  ⚠️ is displacing ITR-filing users looking for a home.
- **Unit economics at demo scale:** retrieval is local (no per-query cost);
  generation at demo volumes is cents/day on free tiers, single-digit
  ₹/client/month on paid mid-tier models.

---

## Verify before presenting

1. ITAT Bengaluru recall (LiveLaw) and SC *Pooja Ramesh Singh v. J&K Bank*
   citation details.
2. GSTR-3B Table-4 ITC hard-locking notification status (say
   "industry-indicated" absent a CBIC notification).
3. ICAI Code of Ethics 13th Edition text on AI, before quoting it.
4. The 44AB→63 dual-Act mapping against the enacted 2025 Act text.
5. Zoho Practice India pricing; ClearTax TaxCloudIndia shutdown status.
6. `GROQ_MODEL` still served (checklist § 2).
