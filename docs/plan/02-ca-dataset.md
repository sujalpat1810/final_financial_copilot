# Feature 02 — Synthetic CA dataset + corpus ingestion

**Branch:** `feat/ca-dataset` · **Depends on:** 01 (doc_id must be client-aware first) · **Status:** pending

## Goal

Generate the entire demo dataset from ONE script with hard assertions, so the
reconciliation demo cannot produce unexpected buckets on stage. Ingest the RAG corpus.
Dataset is FROZEN once this feature merges — later features test against it.

## Files to create

- `scripts/make_ca_dataset.py` — the generator (assertions built in).
- `data/ca_dataset/…` — generated outputs (COMMITTED — they are the demo fixture).
- `data/ca_dataset/manifest.json` — file → {client, doc_type, act_version, fiscal_year, entity}.
- `scripts/ingest_ca_corpus.py` — manifest-driven ingest (reuse `scripts/ingest.py`
  pattern: validate → load models once → ingest each → print summary).
- Check `requirements.txt` for a PDF writer; add `reportlab` (or `fpdf2`) if absent.

## Dataset contents (fictional clients: Mehta Textiles Pvt Ltd, Sharma Electronics Pvt Ltd)

**Structured (for recon — NOT ingested into RAG):**
- `mehta_purchase_register_2025-12.csv` (~40 rows): invoice_no, invoice_date, vendor_name,
  vendor_gstin, taxable_value, cgst, sgst, igst, total. Realistic Indian amounts.
- `mehta_gstr2b_2025-12.csv` (~35 rows, portal-style columns).
- **Planted plan (assert EXACTLY in generator + `tests/test_recon.py` later):**
  - ~25 clean exact matches
  - 3 in-2B-not-books (one = supplier the client never recorded)
  - 4 in-books-not-2B (supplier hasn't filed GSTR-1 → ITC not yet available)
  - 2 amount mismatches (same invoice_no, taxable value differs by ₹1,000 / tax differs)
  - 2 GSTIN/vendor-name mismatches (abbreviated vendor name; one-digit GSTIN typo that
    still fails checksum) — must be caught by FUZZY matching, not exact
  - 1 duplicate invoice in books
  - 1 amendment (B2BA-style: original + amended row in 2B)

**PDFs for RAG (ingested via manifest):**
- 5-6 GST invoices (simple single-page layouts are fine — the viewer highlights text).
  Planted: one invoice whose GSTIN fails the checksum; one whose line items don't sum
  to the stated total.
- `asmt10_mehta.pdf` — ASMT-10 scrutiny notice (s.61 read with Rule 99) flagging an
  ITC discrepancy between GSTR-3B and GSTR-2B for 2025-12, quantified amount, 30-day
  reply direction. Make the discrepancy figure MATCH the planted recon deltas so the
  demo's recon → notice story is internally consistent.
- `mehta_financials_fy2425.pdf` — 2-3 page prior-year P&L + balance-sheet extract.
- Statute extracts (public-domain bare Acts; entity = "CGST Act", client = None):
  `cgst_s16_itc.pdf`, `cgst_s61_rule99_scrutiny.pdf`, `cgst_s73_74_75_demands.pdf`.
- Dual-Act pair: one provision (recommend: business-expenditure disallowance,
  old s.40A / new equivalent) as `itact_1961_extract.pdf` (act_version="1961") and
  `itact_2025_extract.pdf` (act_version="2025"), each with its own section numbering
  and a mapping table. Content paraphrased/faithful-enough for a demo; mark files
  "For demonstration".

**GSTIN rule (for `verify.py` in feature 05 — generator must comply):** 15 chars =
2-digit state code + 10-char PAN + entity code + 'Z' + check char (mod-36 alternating
1/2 weights, standard GSTIN checksum). Generator produces VALID checksums everywhere
except the single planted-invalid invoice + the one typo'd 2B row. Use obviously
fictional PANs (e.g. AAACM1234F pattern) — clients are fictional.

**SQLite seed data** (consumed by feature 05's `structured.py`): `clients.json` +
`deadlines.json` under `data/ca_dataset/seed/` (2 clients; GSTR-3B/GSTR-1/ITR due
dates around the demo period).

## Steps

1. Write generator with the planted plan as data (a literal dict of expected outcomes),
   emit CSVs; assert reading them back reproduces expected bucket counts with a naive
   exact-match pass (pre-validation of the plant). Commit.
2. Emit PDFs; assert planted GSTIN checksum failures/passes. Commit.
3. Write `manifest.json` + `ingest_ca_corpus.py`; run it against a scratch store path
   first, then for real; verify `/documents` lists all docs with client/doc_type. Commit.
4. Sanity queries: one statute question, one client-financials question — confirm
   citations resolve and PDF viewer opens.
5. Commit generated dataset. Freeze.

## Acceptance criteria

- `python scripts/make_ca_dataset.py` runs clean, all internal assertions pass, output
  deterministic (fixed seed).
- Corpus ingested: statutes + notice + financials + invoices retrievable; `/health`
  document count matches manifest.
- Expected-outcome dict is importable (`from scripts.make_ca_dataset import EXPECTED`)
  so `tests/test_recon.py` (feature 05) pins against it.

## Handoff notes

_(fill at session end)_
