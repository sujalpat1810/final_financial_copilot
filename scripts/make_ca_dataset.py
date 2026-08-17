"""
Generate the CA demo dataset — deterministically, with the expected outcomes
asserted before anything is written.

Everything the demo shows is planted here on purpose: the reconciliation
buckets, the duplicate, the amendment, the invalid GSTIN, the invoice whose
lines don't sum. EXPECTED (below) is the single source of truth; the recon
engine's tests import it, so the engine cannot silently drift from the data
and the data cannot drift from the demo script.

Run:  python -m scripts.make_ca_dataset
Everything lands under data/ca_dataset/ and is committed — it is the demo's
fixture, not runtime state.

All names, GSTINs and figures are fictional. GSTINs are structurally valid
(correct check character) except where a failure is deliberately planted.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.verify import gstin_check_char, gstin_checksum_valid  # noqa: E402

OUT = Path("data/ca_dataset")

# ── Cast of characters ────────────────────────────────────────────────────────

CLIENT = "Mehta Textiles Pvt Ltd"
CLIENT_2 = "Sharma Electronics Pvt Ltd"
PERIOD = "2025-12"          # December 2025, FY2025-26
FY = "FY2025-26"


def _gstin(state: str, pan: str, entity_code: str = "1") -> str:
    first14 = f"{state}{pan}{entity_code}Z"
    return first14 + gstin_check_char(first14)


CLIENT_GSTIN = _gstin("27", "AAECM1201F")     # Maharashtra
CLIENT_2_GSTIN = _gstin("07", "AAECS4521K")   # Delhi

# (key, display name, 2B trade name, state, PAN) — trade names sometimes differ
# from the books' vendor names, which is exactly what breaks naive matching.
VENDORS = [
    ("ACM", "Ambika Cotton Mills",      "AMBIKA COTTON MILLS",      "27", "AAACA8842R"),
    ("JSY", "Jalan Synthetics",         "JALAN SYNTHETICS",         "27", "AABCJ3310Q"),
    ("KDC", "Krishna Dyes & Chemicals", "KRISHNA DYES AND CHEMICALS", "24", "AADCK7725H"),
    ("MPK", "Meera Packaging",          "MEERA PACKAGING",          "27", "AAECM9932B"),
    ("NKT", "Nakoda Transport",         "NAKODA TRANSPORT",         "27", "AAFCN1180D"),
    ("OMT", "Omkar Threads",            "OMKAR THREADS",            "33", "AACCO4457E"),
    ("PMS", "Prakash Machinery Spares", "PRAKASH MACHINERY SPARES", "29", "AAHCP6614J"),
    ("RDE", "Riddhi Enterprises",       "RIDDHI ENTERPRISES",       "07", "AAKCR2093L"),
    ("SPS", "Surya Power Solutions",    "SURYA POWER SOLUTIONS",    "27", "AAJCS5548M"),
    ("VYT", "Vardhman Yarn Traders",    "VARDHMAN YARN TRADERS",    "24", "AAECV7031N"),
]
VEND = {v[0]: v for v in VENDORS}


def vendor_gstin(key: str) -> str:
    _, _, _, state, pan = VEND[key]
    return _gstin(state, pan)


def _split_tax(key: str, taxable: int, rate: int) -> tuple[float, float, float]:
    """Intra-state (client is 27/Maharashtra) → CGST+SGST; otherwise IGST."""
    tax = round(taxable * rate / 100, 2)
    if VEND[key][3] == "27":
        return round(tax / 2, 2), round(tax / 2, 2), 0.0
    return 0.0, 0.0, tax


def _row(key: str, inv_no: str, day: int, taxable: int, rate: int = 18,
         gstin: str | None = None, vendor_name: str | None = None) -> dict:
    cgst, sgst, igst = _split_tax(key, taxable, rate)
    return {
        "invoice_no": inv_no,
        "invoice_date": f"2025-12-{day:02d}",
        "vendor_name": vendor_name or VEND[key][1],
        "vendor_gstin": gstin or vendor_gstin(key),
        "taxable_value": taxable,
        "cgst": cgst, "sgst": sgst, "igst": igst,
        "total": round(taxable + cgst + sgst + igst, 2),
    }


def _row2b(key: str, inv_no: str, day: int, taxable: float, rate: int = 18,
           gstin: str | None = None, trade_name: str | None = None,
           doc_kind: str = "B2B", original_invoice_no: str = "") -> dict:
    cgst, sgst, igst = _split_tax(key, taxable, rate)
    return {
        "supplier_gstin": gstin or vendor_gstin(key),
        "trade_name": trade_name or VEND[key][2],
        "invoice_no": inv_no,
        "invoice_date": f"2025-12-{day:02d}",
        "taxable_value": taxable,
        "cgst": cgst, "sgst": sgst, "igst": igst,
        "invoice_value": round(taxable + cgst + sgst + igst, 2),
        "itc_available": "Yes",
        "period": PERIOD,
        "doc_kind": doc_kind,                     # B2B | B2BA
        "original_invoice_no": original_invoice_no,
    }


# ── The planted plan ──────────────────────────────────────────────────────────

def build_rows() -> tuple[list[dict], list[dict]]:
    books: list[dict] = []
    g2b: list[dict] = []

    # 30 clean exact matches — cycle vendors, deterministic values, a few
    # non-18% rates so the slab check has variety.
    rates = [18, 18, 12, 18, 5, 18, 18, 12, 18, 18]
    for i in range(30):
        key = VENDORS[i % 10][0]
        inv_no = f"{key}/2025/{1041 + i}"
        day = 1 + (i * 7) % 27
        taxable = 18000 + ((i * 4735) % 90000)
        rate = rates[i % 10]
        books.append(_row(key, inv_no, day, taxable, rate))
        g2b.append(_row2b(key, inv_no, day, taxable, rate))

    # 1 duplicate in books — ACM/2025/1041 (books[0]) entered twice.
    dup = dict(books[0])
    books.append(dup)

    # 4 in books, not in 2B — suppliers who have not filed GSTR-1.
    for key, inv_no, day, taxable, rate in [
        ("ACM", "ACM/2025/1122", 18, 64000, 18),
        ("KDC", "KDC/2025/0871", 20, 38500, 18),
        ("OMT", "OMT/2025/0455", 22, 27200, 12),
        ("RDE", "RDE/2025/0630", 23, 91000, 18),
    ]:
        books.append(_row(key, inv_no, day, taxable, rate))

    # 2 amount mismatches — same GSTIN + invoice number, different value.
    books.append(_row("JSY", "JSY/2025/0512", 9, 84000, 18))
    g2b.append(_row2b("JSY", "JSY/2025/0512", 9, 88000, 18))
    books.append(_row("SPS", "SPS/2025/0233", 12, 51300, 18))
    g2b.append(_row2b("SPS", "SPS/2025/0233", 12, 51800, 18))

    # 2 GSTIN mismatches — fuzzy-matchable, exact match must fail.
    # (a) 2B carries a one-character GSTIN typo (breaks the checksum too);
    #     invoice number, amount and vendor agree.
    good = vendor_gstin("MPK")
    typo = good[:5] + ("X" if good[5] != "X" else "Y") + good[6:]
    assert not gstin_checksum_valid(typo)
    books.append(_row("MPK", "MPK/2025/0740", 15, 43600, 18))
    g2b.append(_row2b("MPK", "MPK/2025/0740", 15, 43600, 18, gstin=typo))
    # (b) books recorded the vendor's OLD registration (different valid GSTIN);
    #     2B has the current one and an abbreviated trade name.
    old_reg = _gstin("24", "AAECV7031N", entity_code="2")
    books.append(_row("VYT", "VYT/2025/0388", 16, 56000, 18, gstin=old_reg))
    g2b.append(_row2b("VYT", "VYT/2025/0388", 16, 56000, 18,
                      trade_name="VARDHMAN YARN TRDRS"))

    # 1 amendment — supplier amended the invoice upward; books already carry
    # the amended value. 2B shows the B2BA pair; matching must use the amended
    # row and ignore the superseded original.
    books.append(_row("PMS", "PMS/2025/0199", 5, 47500, 18))
    g2b.append(_row2b("PMS", "PMS/2025/0199", 5, 45000, 18, doc_kind="B2BA-ORIGINAL"))
    g2b.append(_row2b("PMS", "PMS/2025/0199", 5, 47500, 18, doc_kind="B2BA",
                      original_invoice_no="PMS/2025/0199"))

    # 3 in 2B, not in books — including one entirely unknown supplier, the
    # "who is this and why are they passing us credit?" demo moment.
    g2b.append(_row2b("JSY", "JSY/2025/0587", 26, 33000, 18))
    g2b.append(_row2b("NKT", "NKT/2025/0912", 27, 12400, 5))
    zen = _gstin("27", "AAQCZ8814P")
    g2b.append(_row2b("NKT", "ZM/2025/0077", 28, 158000, 18,
                      gstin=zen, trade_name="ZENITH METALS"))

    return books, g2b


BOOKS_ROWS, G2B_ROWS = build_rows()

# ── ITC at risk: what the ASMT-10 quantifies ──────────────────────────────────
# Tax on invoices claimed in books but absent from GSTR-2B, plus the tax on the
# amount-mismatch deltas. Computed from the rows, so the notice figure and the
# reconciliation output can never disagree.

def _tax(row: dict) -> float:
    return round(row["cgst"] + row["sgst"] + row["igst"], 2)


_books_only_tax = sum(_tax(r) for r in BOOKS_ROWS
                      if r["invoice_no"] in ("ACM/2025/1122", "KDC/2025/0871",
                                             "OMT/2025/0455", "RDE/2025/0630"))
_mismatch_delta_tax = round((88000 - 84000) * 0.18 + (51800 - 51300) * 0.18, 2)
ITC_AT_RISK = round(_books_only_tax + _mismatch_delta_tax, 2)


EXPECTED = {
    "client": CLIENT,
    "period": PERIOD,
    "books_rows": 40,   # 30 exact + 1 duplicate + 4 books-only + 2 amount + 2 gstin + 1 amendment
    "gstr2b_rows": 39,           # counting the B2BA pair as two rows
    "exact_matches": 30,
    "amendment_resolved": 1,     # PMS/2025/0199 matched against the B2BA row
    "fuzzy_matches": 2,          # both become gstin_mismatch exceptions
    "exceptions": {
        "in_2b_not_books": 3,
        "in_books_not_2b": 4,
        "amount_mismatch": 2,
        "gstin_mismatch": 2,
        "duplicate_in_books": 1,
    },
    "itc_at_risk": ITC_AT_RISK,
    "planted": {
        "duplicate_invoice_no": "ACM/2025/1041",
        "amendment_invoice_no": "PMS/2025/0199",
        "unknown_supplier": "ZENITH METALS",
        "invalid_gstin_invoice_pdf": "invoice_mpk_0733.pdf",   # supplier GSTIN fails checksum
        "bad_total_invoice_pdf": "invoice_jsy_0505.pdf",       # line items don't sum
    },
}


# ── Pre-write validation of the plant ────────────────────────────────────────

def _validate_plant() -> None:
    assert len(BOOKS_ROWS) == EXPECTED["books_rows"], len(BOOKS_ROWS)
    assert len(G2B_ROWS) == EXPECTED["gstr2b_rows"], len(G2B_ROWS)

    # Naive exact matcher over (gstin, invoice_no, taxable) reproduces the plan.
    def k3(r, gk):
        return (r[gk], r["invoice_no"], round(float(r["taxable_value"]), 2))

    b_keys = [k3(r, "vendor_gstin") for r in BOOKS_ROWS]
    g_keys = [k3(r, "supplier_gstin") for r in G2B_ROWS
              if r["doc_kind"] != "B2BA-ORIGINAL"]      # amendment: amended row wins
    exact = set(b_keys) & set(g_keys)
    assert len(exact) == 31, len(exact)   # 30 clean + 1 amendment-resolved

    # The duplicate is a real duplicate.
    assert b_keys.count(k3(BOOKS_ROWS[0], "vendor_gstin")) == 2

    # Every GSTIN passes the checksum except the single planted typo.
    bad_b = [r for r in BOOKS_ROWS if not gstin_checksum_valid(r["vendor_gstin"])]
    bad_g = [r for r in G2B_ROWS if not gstin_checksum_valid(r["supplier_gstin"])]
    assert bad_b == [], [r["invoice_no"] for r in bad_b]
    assert len(bad_g) == 1 and bad_g[0]["invoice_no"] == "MPK/2025/0740"

    # Books-only and 2B-only counts.
    only_books = [k for k in set(b_keys) if k not in set(g_keys)]
    only_g = [k for k in set(g_keys) if k not in set(b_keys)]
    # books-only: 4 planted + 2 amount-mismatch + 2 gstin-mismatch = 8 unmatched-by-naive
    assert len(only_books) == 8, sorted(only_books)
    # 2B-only: 3 planted + their mismatch counterparts = 7
    assert len(only_g) == 7, sorted(only_g)

    assert ITC_AT_RISK > 0


# ── Writers ───────────────────────────────────────────────────────────────────

def write_csvs() -> None:
    reg = OUT / f"mehta_purchase_register_{PERIOD}.csv"
    with open(reg, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(BOOKS_ROWS[0].keys()))
        w.writeheader()
        w.writerows(BOOKS_ROWS)

    g2b = OUT / f"mehta_gstr2b_{PERIOD}.csv"
    with open(g2b, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(G2B_ROWS[0].keys()))
        w.writeheader()
        w.writerows(G2B_ROWS)


# ---- PDFs -------------------------------------------------------------------

def _pdf(title: str):
    from datetime import datetime, timezone
    from fpdf import FPDF
    doc = FPDF(format="A4")
    # Fixed creation date → regenerating the dataset produces identical bytes,
    # so git only sees changes when the content actually changed.
    doc.set_creation_date(datetime(2026, 8, 1, tzinfo=timezone.utc))
    doc.set_auto_page_break(auto=True, margin=18)
    doc.add_page()
    doc.set_font("helvetica", "B", 13)
    doc.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    doc.ln(2)
    doc.set_font("helvetica", size=10)
    return doc


def _para(doc, text: str, style: str = "", size: int = 10) -> None:
    doc.set_font("helvetica", style, size)
    doc.multi_cell(0, 5.5, text)
    doc.ln(1.5)


_DEMO_FOOTER = ("Extract simplified for demonstration. All parties, figures and "
                "GSTINs are fictional. Verify statutory text against the "
                "enacted version before reliance.")


def write_invoice_pdfs() -> None:
    # Two planted-defect invoices + four clean ones.
    specs = [
        # (filename, vendor key, inv_no, day, lines [(desc, qty, unit_price)],
        #  rate, force_gstin, stated_taxable_override)
        ("invoice_acm_1041.pdf", "ACM", "ACM/2025/1041", 1,
         [("Combed cotton yarn 40s, carton", 40, 450.0)], 18, None, None),
        ("invoice_jsy_0512.pdf", "JSY", "JSY/2025/0512", 9,
         [("Polyester filament yarn, spool", 240, 350.0)], 18, None, None),
        ("invoice_omt_0455.pdf", "OMT", "OMT/2025/0455", 22,
         [("Sewing thread, industrial cone", 340, 80.0)], 12, None, None),
        ("invoice_sps_0233.pdf", "SPS", "SPS/2025/0233", 12,
         [("Servo stabiliser 25 kVA", 1, 51300.0)], 18, None, None),
        # PLANT: supplier GSTIN fails the checksum.
        ("invoice_mpk_0733.pdf", "MPK", "MPK/2025/0733", 8,
         [("Corrugated boxes, 5-ply", 800, 42.0)], 18, "BAD", None),
        # PLANT: line items sum to 33,600 but the invoice states 36,100.
        ("invoice_jsy_0505.pdf", "JSY", "JSY/2025/0505", 4,
         [("Dyed viscose yarn, spool", 60, 310.0),
          ("Freight and handling", 1, 15000.0)], 18, None, 36100.0),
    ]
    for fname, key, inv_no, day, lines, rate, force_gstin, stated in specs:
        _, name, _, state, _ = VEND[key]
        supplier_gstin = vendor_gstin(key)
        if force_gstin == "BAD":
            supplier_gstin = supplier_gstin[:14] + (
                "0" if supplier_gstin[14] != "0" else "1")
            assert not gstin_checksum_valid(supplier_gstin)

        line_total = round(sum(q * p for _, q, p in lines), 2)
        taxable = stated if stated is not None else line_total
        cgst, sgst, igst = _split_tax(key, taxable, rate)

        doc = _pdf("TAX INVOICE")
        _para(doc, f"Supplier: {name}\nGSTIN: {supplier_gstin}\n"
                   f"State code: {state}")
        _para(doc, f"Buyer: {CLIENT}\nGSTIN: {CLIENT_GSTIN}\nState code: 27")
        _para(doc, f"Invoice number: {inv_no}    Invoice date: 2025-12-{day:02d}",
              style="B")
        doc.set_font("helvetica", "B", 10)
        doc.cell(90, 6, "Description", border=1)
        doc.cell(25, 6, "Qty", border=1)
        doc.cell(35, 6, "Rate (Rs.)", border=1)
        doc.cell(35, 6, "Amount (Rs.)", border=1, new_x="LMARGIN", new_y="NEXT")
        doc.set_font("helvetica", size=10)
        for desc, qty, price in lines:
            doc.cell(90, 6, desc, border=1)
            doc.cell(25, 6, str(qty), border=1)
            doc.cell(35, 6, f"{price:,.2f}", border=1)
            doc.cell(35, 6, f"{qty * price:,.2f}", border=1,
                     new_x="LMARGIN", new_y="NEXT")
        doc.ln(3)
        _para(doc, f"Taxable value: Rs. {taxable:,.2f}\n"
                   f"CGST: Rs. {cgst:,.2f}    SGST: Rs. {sgst:,.2f}    "
                   f"IGST: Rs. {igst:,.2f}\n"
                   f"Invoice total: Rs. {taxable + cgst + sgst + igst:,.2f}",
              style="B")
        _para(doc, _DEMO_FOOTER, size=8)
        doc.output(str(OUT / fname))


def write_notice_pdf() -> None:
    doc = _pdf("FORM GST ASMT-10")
    _para(doc, "[See rule 99(1)]  Notice for intimating discrepancies in the "
               "return after scrutiny", style="I")
    _para(doc, f"Reference no.: ZD2708260012345X    Date: 2026-08-05\n"
               f"To: {CLIENT}\nGSTIN: {CLIENT_GSTIN}\n"
               f"Tax period: December 2025 (FY 2025-26)    Return: GSTR-3B")
    _para(doc, "Discrepancies observed:", style="B")
    _para(doc, "1. Scrutiny of the return under section 61 of the CGST Act, "
               "2017 read with rule 99 of the CGST Rules, 2017 indicates that "
               "input tax credit of Rs. {:,.2f} availed in Table 4(A)(5) of "
               "FORM GSTR-3B for the tax period December 2025 is in excess of "
               "the credit auto-populated in FORM GSTR-2B for the said period. "
               "The excess appears attributable to (a) invoices not reported "
               "by the corresponding suppliers in their FORM GSTR-1, and (b) "
               "differences in taxable value between the credit availed and "
               "the value reported by suppliers.".format(ITC_AT_RISK))
    _para(doc, "You are hereby directed to furnish an explanation for the "
               "above discrepancies within thirty days. If the explanation is "
               "found acceptable, you will be informed in FORM GST ASMT-12. "
               "If no satisfactory explanation is furnished within the period, "
               "or if you fail to take corrective action after accepting the "
               "discrepancies, proceedings may be initiated under section 73 "
               "or section 74 of the Act.")
    _para(doc, "Reply is to be furnished in FORM GST ASMT-11.", style="B")
    _para(doc, "Proper Officer (fictional)\nWard 27, Demonstration Jurisdiction")
    _para(doc, _DEMO_FOOTER, size=8)
    doc.output(str(OUT / "asmt10_mehta.pdf"))


def write_financials_pdf() -> None:
    doc = _pdf(f"{CLIENT} - Statement of Profit and Loss, FY 2024-25")
    _para(doc, "(Rs. in lakh, standalone, audited)", style="I")
    rows = [
        ("Revenue from operations", "4,812.60", "4,105.34"),
        ("Other income", "38.12", "29.80"),
        ("Total income", "4,850.72", "4,135.14"),
        ("Cost of materials consumed", "3,120.45", "2,704.90"),
        ("Employee benefits expense", "486.30", "441.75"),
        ("Finance costs", "92.18", "104.62"),
        ("Depreciation and amortisation", "118.44", "112.09"),
        ("Other expenses", "612.77", "530.41"),
        ("Total expenses", "4,430.14", "3,893.77"),
        ("Profit before tax", "420.58", "241.37"),
        ("Tax expense", "106.02", "62.51"),
        ("Profit for the year", "314.56", "178.86"),
    ]
    doc.set_font("helvetica", "B", 10)
    doc.cell(95, 6, "Particulars", border=1)
    doc.cell(40, 6, "FY 2024-25", border=1)
    doc.cell(40, 6, "FY 2023-24", border=1, new_x="LMARGIN", new_y="NEXT")
    doc.set_font("helvetica", size=10)
    for name, cur, prev in rows:
        doc.cell(95, 6, name, border=1)
        doc.cell(40, 6, cur, border=1)
        doc.cell(40, 6, prev, border=1, new_x="LMARGIN", new_y="NEXT")
    doc.ln(4)
    doc.add_page()
    doc.set_font("helvetica", "B", 13)
    doc.cell(0, 8, f"{CLIENT} - Balance Sheet extract, as at 31 March 2025",
             new_x="LMARGIN", new_y="NEXT")
    doc.ln(2)
    _para(doc, "(Rs. in lakh, standalone, audited)", style="I")
    bs = [
        ("Property, plant and equipment", "1,284.20"),
        ("Inventories", "976.55"),
        ("Trade receivables", "1,102.31"),
        ("Cash and cash equivalents", "148.09"),
        ("Trade payables (of which MSME: 212.40)", "864.77"),
        ("Borrowings", "701.00"),
        ("Equity share capital", "250.00"),
        ("Other equity", "1,795.38"),
    ]
    doc.set_font("helvetica", size=10)
    for name, val in bs:
        doc.cell(120, 6, name, border=1)
        doc.cell(45, 6, val, border=1, new_x="LMARGIN", new_y="NEXT")
    doc.ln(4)
    _para(doc, _DEMO_FOOTER, size=8)
    doc.output(str(OUT / "mehta_financials_fy2425.pdf"))


def write_statute_pdfs() -> None:
    # s.16 CGST — ITC conditions.
    doc = _pdf("CGST Act, 2017 - Section 16: Eligibility and conditions for "
               "taking input tax credit (extract)")
    _para(doc, "16(1). Every registered person shall, subject to such "
               "conditions and restrictions as may be prescribed, be entitled "
               "to take credit of input tax charged on any supply of goods or "
               "services or both to him which are used or intended to be used "
               "in the course or furtherance of his business.")
    _para(doc, "16(2). Notwithstanding anything contained in this section, no "
               "registered person shall be entitled to the credit of any input "
               "tax in respect of any supply of goods or services or both to "
               "him unless -\n"
               "(a) he is in possession of a tax invoice or debit note issued "
               "by a supplier registered under this Act;\n"
               "(aa) the details of the invoice or debit note have been "
               "furnished by the supplier in the statement of outward supplies "
               "and such details have been communicated to the recipient in "
               "the manner specified under section 37;\n"
               "(b) he has received the goods or services or both;\n"
               "(ba) the details of input tax credit communicated to such "
               "registered person under section 38 have not been restricted;\n"
               "(c) subject to section 41, the tax charged in respect of such "
               "supply has been actually paid to the Government; and\n"
               "(d) he has furnished the return under section 39.")
    _para(doc, "16(4). A registered person shall not be entitled to take input "
               "tax credit in respect of any invoice or debit note for supply "
               "of goods or services or both after the thirtieth day of "
               "November following the end of the financial year to which such "
               "invoice or debit note pertains or furnishing of the relevant "
               "annual return, whichever is earlier.")
    _para(doc, "Practical effect: credit is available only where the supplier "
               "has reported the invoice (so it appears in FORM GSTR-2B) and "
               "the other conditions of sub-section (2) are met. An invoice "
               "recorded in the recipient's books but absent from GSTR-2B "
               "fails condition (aa) until the supplier reports it.", style="I")
    _para(doc, _DEMO_FOOTER, size=8)
    doc.output(str(OUT / "cgst_s16_itc.pdf"))

    # s.61 + Rule 99 — scrutiny.
    doc = _pdf("CGST Act, 2017 - Section 61 and Rule 99: Scrutiny of returns "
               "(extract)")
    _para(doc, "61(1). The proper officer may scrutinise the return and "
               "related particulars furnished by the registered person to "
               "verify the correctness of the return and inform him of the "
               "discrepancies noticed, if any, in such manner as may be "
               "prescribed and seek his explanation thereto.")
    _para(doc, "61(2). In case the explanation is found acceptable, the "
               "registered person shall be informed accordingly and no further "
               "action shall be taken in this regard.")
    _para(doc, "61(3). In case no satisfactory explanation is furnished within "
               "a period of thirty days of being informed by the proper "
               "officer or such further period as may be permitted by him, or "
               "where the registered person, after accepting the "
               "discrepancies, fails to take the corrective measure in his "
               "return for the month in which the discrepancy is accepted, the "
               "proper officer may initiate appropriate action including "
               "action under section 65 or section 66 or section 67, or "
               "proceed to determine the tax and other dues under section 73 "
               "or section 74.")
    _para(doc, "Rule 99(1). Where any return furnished by a registered person "
               "is selected for scrutiny, the proper officer shall scrutinise "
               "the same in accordance with the provisions of section 61 with "
               "reference to the information available with him, and in case "
               "of any discrepancy, he shall issue a notice to the said person "
               "in FORM GST ASMT-10, informing him of such discrepancy and "
               "seeking his explanation thereto within such time, not "
               "exceeding thirty days from the date of service of the notice.")
    _para(doc, "Rule 99(2). The registered person may accept the discrepancy "
               "mentioned in the notice issued under sub-rule (1), and pay the "
               "tax, interest and any other amount arising from such "
               "discrepancy and inform the same or furnish an explanation for "
               "the discrepancy in FORM GST ASMT-11 to the proper officer.")
    _para(doc, "Rule 99(3). Where the explanation furnished by the registered "
               "person or the information submitted under sub-rule (2) is "
               "found to be acceptable, the proper officer shall inform him "
               "accordingly in FORM GST ASMT-12.")
    _para(doc, _DEMO_FOOTER, size=8)
    doc.output(str(OUT / "cgst_s61_rule99_scrutiny.pdf"))

    # s.73/74/75 — demands and adjudication.
    doc = _pdf("CGST Act, 2017 - Sections 73, 74 and 75: Determination of tax "
               "(extract)")
    _para(doc, "73(1). Where it appears to the proper officer that any tax has "
               "not been paid or short paid or erroneously refunded, or where "
               "input tax credit has been wrongly availed or utilised for any "
               "reason, other than the reason of fraud or any wilful "
               "misstatement or suppression of facts to evade tax, he shall "
               "serve notice on the person chargeable with tax requiring him "
               "to show cause as to why he should not pay the amount specified "
               "in the notice along with interest payable thereon under "
               "section 50 and a penalty leviable under the provisions of this "
               "Act or the rules made thereunder.")
    _para(doc, "74(1). Where it appears to the proper officer that any tax has "
               "not been paid or short paid or erroneously refunded or where "
               "input tax credit has been wrongly availed or utilised by "
               "reason of fraud, or any wilful misstatement or suppression of "
               "facts to evade tax, he shall serve notice on the person "
               "chargeable with tax requiring him to show cause as to why he "
               "should not pay the amount specified in the notice along with "
               "interest payable thereon under section 50 and a penalty "
               "equivalent to the tax specified in the notice.")
    _para(doc, "75(4). An opportunity of hearing shall be granted where a "
               "request is received in writing from the person chargeable with "
               "tax or penalty, or where any adverse decision is contemplated "
               "against such person.", style="B")
    _para(doc, "75(7). The amount of tax, interest and penalty demanded in the "
               "order shall not be in excess of the amount specified in the "
               "notice and no demand shall be confirmed on the grounds other "
               "than the grounds specified in the notice.")
    _para(doc, _DEMO_FOOTER, size=8)
    doc.output(str(OUT / "cgst_s73_74_75_demands.pdf"))


def write_dual_act_pdfs() -> None:
    doc = _pdf("Income-tax Act, 1961 - Section 44AB: Audit of accounts "
               "(extract, as applicable for AY 2026-27)")
    _para(doc, "44AB. Every person, -\n"
               "(a) carrying on business shall, if his total sales, turnover "
               "or gross receipts, as the case may be, in business exceed or "
               "exceeds one crore rupees in any previous year:\n"
               "Provided that in the case of a person whose aggregate of all "
               "amounts received including amount received for sales, turnover "
               "or gross receipts during the previous year, in cash, does not "
               "exceed five per cent of the said amount, and aggregate of all "
               "payments made including amount incurred for expenditure, in "
               "cash, during the previous year does not exceed five per cent "
               "of the said payment, this clause shall have effect as if for "
               "the words 'one crore rupees', the words 'ten crore rupees' had "
               "been substituted; or\n"
               "(b) carrying on profession shall, if his gross receipts in "
               "profession exceed fifty lakh rupees in any previous year,\n"
               "get his accounts of such previous year audited by an "
               "accountant before the specified date and furnish by that date "
               "the report of such audit in the prescribed form.")
    _para(doc, "Returns for assessment year 2026-27 (financial year 2025-26) "
               "continue to be governed by the Income-tax Act, 1961 and this "
               "section numbering.", style="I")
    _para(doc, _DEMO_FOOTER, size=8)
    doc.output(str(OUT / "itact_1961_s44ab.pdf"))

    doc = _pdf("Income-tax Act, 2025 - Section 63: Audit of accounts "
               "(demonstration extract, tax year 2026-27 onwards)")
    _para(doc, "63(1). Every person carrying on business shall get the "
               "accounts of the tax year audited by an accountant before the "
               "specified date, if the total sales, turnover or gross receipts "
               "in business exceed one crore rupees in the tax year:\n"
               "Provided that where the aggregate of amounts received in cash "
               "does not exceed five per cent of total receipts, and the "
               "aggregate of payments made in cash does not exceed five per "
               "cent of total payments, this sub-section shall have effect as "
               "if for the words 'one crore rupees', the words 'ten crore "
               "rupees' had been substituted.")
    _para(doc, "63(2). Every person carrying on profession shall get the "
               "accounts of the tax year audited if gross receipts in the "
               "profession exceed fifty lakh rupees in the tax year.")
    _para(doc, "Mapping note: Income-tax Act, 1961 section 44AB corresponds to "
               "Income-tax Act, 2025 section 63. The 2025 Act replaces "
               "'previous year' and 'assessment year' with the single defined "
               "term 'tax year' and applies from tax year 2026-27 (returns "
               "filed from July 2027). VERIFY section numbering against the "
               "enacted text before any professional use.", style="B")
    _para(doc, _DEMO_FOOTER, size=8)
    doc.output(str(OUT / "itact_2025_s63.pdf"))


def write_seed_and_manifest() -> None:
    seed = OUT / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    (seed / "clients.json").write_text(json.dumps([
        {"client_id": "mehta", "name": CLIENT, "gstin": CLIENT_GSTIN,
         "pan": "AAECM1201F", "state_code": "27"},
        {"client_id": "sharma", "name": CLIENT_2, "gstin": CLIENT_2_GSTIN,
         "pan": "AAECS4521K", "state_code": "07"},
    ], indent=2), encoding="utf-8")
    (seed / "deadlines.json").write_text(json.dumps([
        {"client_id": "mehta", "form": "GSTR-3B", "period": "2026-07",
         "due_date": "2026-08-20", "status": "open"},
        {"client_id": "mehta", "form": "ASMT-11 reply", "period": "2025-12",
         "due_date": "2026-09-04", "status": "open"},
        {"client_id": "mehta", "form": "ITR (audit case)", "period": "AY 2026-27",
         "due_date": "2026-10-31", "status": "open"},
        {"client_id": "sharma", "form": "GSTR-3B", "period": "2026-07",
         "due_date": "2026-08-20", "status": "open"},
        {"client_id": "sharma", "form": "Tax audit report (3CB-3CD)",
         "period": "AY 2026-27", "due_date": "2026-09-30", "status": "open"},
    ], indent=2), encoding="utf-8")

    manifest = [
        # Statutes — firm knowledge, no client.
        {"file": "data/ca_dataset/cgst_s16_itc.pdf",
         "doc_name": "CGST Act s.16 - Input tax credit",
         "entity": "CGST Act", "fiscal_year": FY, "doc_type": "statute"},
        {"file": "data/ca_dataset/cgst_s61_rule99_scrutiny.pdf",
         "doc_name": "CGST Act s.61 + Rule 99 - Scrutiny of returns",
         "entity": "CGST Act", "fiscal_year": FY, "doc_type": "statute"},
        {"file": "data/ca_dataset/cgst_s73_74_75_demands.pdf",
         "doc_name": "CGST Act s.73-75 - Demands and adjudication",
         "entity": "CGST Act", "fiscal_year": FY, "doc_type": "statute"},
        {"file": "data/ca_dataset/itact_1961_s44ab.pdf",
         "doc_name": "Income-tax Act 1961 s.44AB - Tax audit",
         "entity": "Income-tax Act", "fiscal_year": FY,
         "doc_type": "statute", "act_version": "1961"},
        {"file": "data/ca_dataset/itact_2025_s63.pdf",
         "doc_name": "Income-tax Act 2025 s.63 - Tax audit",
         "entity": "Income-tax Act", "fiscal_year": FY,
         "doc_type": "statute", "act_version": "2025"},
        # Client documents.
        {"file": "data/ca_dataset/asmt10_mehta.pdf",
         "doc_name": "ASMT-10 scrutiny notice - Dec 2025",
         "entity": CLIENT, "fiscal_year": FY,
         "client": CLIENT, "doc_type": "notice"},
        {"file": "data/ca_dataset/mehta_financials_fy2425.pdf",
         "doc_name": "Financial statements FY2024-25",
         "entity": CLIENT, "fiscal_year": "FY2024-25",
         "client": CLIENT, "doc_type": "financials"},
    ] + [
        {"file": f"data/ca_dataset/{fname}",
         "doc_name": f"Purchase invoice {inv}",
         "entity": CLIENT, "fiscal_year": FY,
         "client": CLIENT, "doc_type": "invoice"}
        for fname, inv in [
            ("invoice_acm_1041.pdf", "ACM/2025/1041"),
            ("invoice_jsy_0512.pdf", "JSY/2025/0512"),
            ("invoice_omt_0455.pdf", "OMT/2025/0455"),
            ("invoice_sps_0233.pdf", "SPS/2025/0233"),
            ("invoice_mpk_0733.pdf", "MPK/2025/0733"),
            ("invoice_jsy_0505.pdf", "JSY/2025/0505"),
        ]
    ]
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                       encoding="utf-8")
    (OUT / "expected.json").write_text(json.dumps(EXPECTED, indent=2),
                                       encoding="utf-8")


def main() -> int:
    _validate_plant()
    OUT.mkdir(parents=True, exist_ok=True)
    write_csvs()
    write_invoice_pdfs()
    write_notice_pdf()
    write_financials_pdf()
    write_statute_pdfs()
    write_dual_act_pdfs()
    write_seed_and_manifest()
    print(f"dataset written to {OUT}/")
    print(f"  books rows        : {len(BOOKS_ROWS)}")
    print(f"  gstr-2b rows      : {len(G2B_ROWS)}")
    print(f"  itc at risk       : Rs. {ITC_AT_RISK:,.2f}")
    print(f"  expected buckets  : {EXPECTED['exceptions']}")
    print("  all plant assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
