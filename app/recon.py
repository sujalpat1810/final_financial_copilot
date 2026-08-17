"""
GSTR-2B ↔ purchase-register reconciliation — deterministic, pure Python.

The design rule this module lives by: THE LLM NEVER TOUCHES A NUMBER.  Matching
is exact-key joins, then bounded fuzzy joins, all reproducible: the same two
files produce the same matches, the same buckets, and the same totals every
run.  That is what lets the output be presented to a chartered accountant as a
working paper rather than a suggestion.  The model's only job, downstream of
this module, is to EXPLAIN an exception — never to find, score, or sum one.

Pure functions over plain dicts: no FastAPI, no SQLite, no config imports.
tests/test_recon.py pins the output against the dataset generator's EXPECTED
dict, so the engine and the demo data cannot drift apart silently.

Matching pipeline
─────────────────
0. normalise      strip/upper GSTINs, canonicalise invoice numbers, parse
                  amounts; resolve GSTR-2B amendments (a B2BA row supersedes
                  its original — matching against a superseded value would
                  "find" a mismatch the supplier already fixed)
1. duplicates     the same (GSTIN, invoice no) twice in BOOKS is flagged
                  before matching, else the duplicate would double-claim ITC
                  by matching the same 2B row twice
2. exact          (GSTIN, invoice no, taxable value) equality
3. amount pass    (GSTIN, invoice no) equal but value differs
                  -> amount_mismatch exception carrying the delta
4. fuzzy          invoice no + value equal, GSTIN differs — matched by GSTIN
                  edit-distance <= 2 or vendor-name similarity >= 0.75
                  -> gstin_mismatch exception (a real pairing, wrong identity)
5. residue        books-only rows  -> in_books_not_2b   (ITC not yet in 2B)
                  2B-only rows     -> in_2b_not_books   (unrecorded, or not
                                                          the client's credit)
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

# ── Loading ───────────────────────────────────────────────────────────────────

_NUMERIC = ("taxable_value", "cgst", "sgst", "igst", "total", "invoice_value")


def _parse_row(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = dict(row)
    for key in _NUMERIC:
        if key in out and out[key] not in (None, ""):
            out[key] = round(float(str(out[key]).replace(",", "")), 2)
    return out


def load_register(path: str) -> list[dict[str, Any]]:
    """Purchase register rows: vendor_gstin, invoice_no, taxable_value, taxes."""
    with open(path, newline="", encoding="utf-8") as f:
        return [_parse_row(r) for r in csv.DictReader(f)]


def load_gstr2b(path: str) -> list[dict[str, Any]]:
    """GSTR-2B rows: supplier_gstin, invoice_no, taxable_value, doc_kind."""
    with open(path, newline="", encoding="utf-8") as f:
        return [_parse_row(r) for r in csv.DictReader(f)]


# ── Normalisation ─────────────────────────────────────────────────────────────

def _norm_gstin(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").upper())


def _norm_invoice(value: str | None) -> str:
    """
    Canonical invoice number: uppercase with separators removed.  Books and
    portal data routinely write the same invoice as INV/2025/1041, INV-2025-1041
    and INV 2025 1041 — separator choice is formatting, not identity.
    """
    v = (value or "").upper()
    v = re.sub(r"[\s\-_/\\.]+", "", v)
    return v


def _tax(row: dict[str, Any]) -> float:
    return round(float(row.get("cgst") or 0) + float(row.get("sgst") or 0)
                 + float(row.get("igst") or 0), 2)


def resolve_amendments(g2b_rows: list[dict[str, Any]]) -> tuple[
        list[dict[str, Any]], int]:
    """
    Collapse B2BA amendment pairs: where an amended row exists for an invoice,
    the original is superseded and must not take part in matching.  Returns
    (effective rows, how many originals were superseded).
    """
    amended_keys = {
        (_norm_gstin(r.get("supplier_gstin")), _norm_invoice(r.get("invoice_no")))
        for r in g2b_rows if (r.get("doc_kind") or "").upper() == "B2BA"
    }
    effective, superseded = [], 0
    for r in g2b_rows:
        kind = (r.get("doc_kind") or "").upper()
        key = (_norm_gstin(r.get("supplier_gstin")),
               _norm_invoice(r.get("invoice_no")))
        # Any non-amendment row whose invoice has an amended counterpart is
        # superseded — covers explicit "B2BA-ORIGINAL" markers and a plain B2B
        # row an amendment replaced.
        if kind != "B2BA" and key in amended_keys:
            superseded += 1
            continue
        effective.append(r)
    return effective, superseded


# ── Fuzzy helpers ─────────────────────────────────────────────────────────────

def _edit_distance_leq(a: str, b: str, limit: int) -> bool:
    """Cheap bounded edit distance — GSTINs are 15 chars, this is plenty."""
    if abs(len(a) - len(b)) > limit:
        return False
    if a == b:
        return True
    # For equal-length strings (the GSTIN case) substitutions dominate.
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) <= limit
    return SequenceMatcher(None, a, b).ratio() >= 1 - (limit / max(len(a), len(b)))


def _name_similarity(a: str | None, b: str | None) -> float:
    an = re.sub(r"[^A-Z0-9 ]", "", (a or "").upper()).strip()
    bn = re.sub(r"[^A-Z0-9 ]", "", (b or "").upper()).strip()
    if not an or not bn:
        return 0.0
    return SequenceMatcher(None, an, bn).ratio()


# ── Result shape ──────────────────────────────────────────────────────────────

@dataclass
class ReconResult:
    matches: list[dict[str, Any]] = field(default_factory=list)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


BUCKETS = ("in_2b_not_books", "in_books_not_2b", "amount_mismatch",
           "gstin_mismatch", "duplicate_in_books")


# ── The engine ────────────────────────────────────────────────────────────────

def reconcile(books_rows: list[dict[str, Any]],
              g2b_rows: list[dict[str, Any]],
              amount_tol: float = 1.0,
              gstin_edit_limit: int = 2,
              name_sim_floor: float = 0.75) -> ReconResult:
    """
    Deterministic reconciliation.  amount_tol absorbs rupee rounding between
    systems; anything beyond it is a real mismatch, reported, never absorbed.
    """
    result = ReconResult()
    checks = {"gstin_valid_books": 0, "gstin_total_books": 0,
              "gstin_valid_2b": 0, "gstin_total_2b": 0,
              "tax_split_ok": 0, "tax_split_total": 0}

    # Deterministic per-row verification for the badges — pure verify.py calls.
    from app.verify import gstin_checksum_valid, tax_split_consistent
    for r in books_rows:
        checks["gstin_total_books"] += 1
        if gstin_checksum_valid(r.get("vendor_gstin")):
            checks["gstin_valid_books"] += 1
        checks["tax_split_total"] += 1
        if tax_split_consistent(float(r.get("cgst") or 0),
                                float(r.get("sgst") or 0),
                                float(r.get("igst") or 0)):
            checks["tax_split_ok"] += 1
    for r in g2b_rows:
        checks["gstin_total_2b"] += 1
        if gstin_checksum_valid(r.get("supplier_gstin")):
            checks["gstin_valid_2b"] += 1

    # Step 0 — amendments.
    g2b_effective, superseded = resolve_amendments(g2b_rows)

    # Step 1 — duplicates in books, flagged before matching.
    seen: dict[tuple[str, str], int] = {}
    books_effective: list[dict[str, Any]] = []
    for r in books_rows:
        key = (_norm_gstin(r.get("vendor_gstin")),
               _norm_invoice(r.get("invoice_no")))
        if key in seen:
            result.exceptions.append({
                "bucket": "duplicate_in_books",
                "books_row": r, "g2b_row": None,
                "delta": {"duplicate_of_row": seen[key],
                          "itc_at_risk": _tax(r)},
            })
            continue
        seen[key] = len(books_effective)
        books_effective.append(r)

    # Index the effective 2B rows by their exact key.
    g2b_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for r in g2b_effective:
        key = (_norm_gstin(r.get("supplier_gstin")),
               _norm_invoice(r.get("invoice_no")))
        g2b_by_key[key] = r

    matched_g2b_keys: set[tuple[str, str]] = set()
    unmatched_books: list[dict[str, Any]] = []
    amendment_resolved = 0

    # Steps 2–3 — exact key, then amount comparison.
    for b in books_effective:
        key = (_norm_gstin(b.get("vendor_gstin")),
               _norm_invoice(b.get("invoice_no")))
        g = g2b_by_key.get(key)
        if g is None:
            unmatched_books.append(b)
            continue
        matched_g2b_keys.add(key)
        delta_value = round(float(g["taxable_value"]) - float(b["taxable_value"]), 2)
        if abs(delta_value) <= amount_tol:
            was_amended = (g.get("doc_kind") or "").upper() == "B2BA"
            if was_amended:
                amendment_resolved += 1
            result.matches.append({
                "books_row": b, "g2b_row": g,
                "match_type": "amended" if was_amended else "exact",
                "score": 1.0,
            })
        else:
            result.exceptions.append({
                "bucket": "amount_mismatch",
                "books_row": b, "g2b_row": g,
                "delta": {
                    "taxable_value_books": b["taxable_value"],
                    "taxable_value_2b": g["taxable_value"],
                    "delta_taxable": delta_value,
                    "delta_tax": round(_tax(g) - _tax(b), 2),
                },
            })

    # Step 4 — fuzzy: invoice+value equal, GSTIN differs.
    remaining_g2b = [
        r for r in g2b_effective
        if (_norm_gstin(r.get("supplier_gstin")),
            _norm_invoice(r.get("invoice_no"))) not in matched_g2b_keys
    ]
    still_unmatched_books: list[dict[str, Any]] = []
    for b in unmatched_books:
        b_inv = _norm_invoice(b.get("invoice_no"))
        b_gstin = _norm_gstin(b.get("vendor_gstin"))
        candidate = None
        for g in remaining_g2b:
            if _norm_invoice(g.get("invoice_no")) != b_inv:
                continue
            if abs(float(g["taxable_value"]) - float(b["taxable_value"])) > amount_tol:
                continue
            g_gstin = _norm_gstin(g.get("supplier_gstin"))
            if (_edit_distance_leq(b_gstin, g_gstin, gstin_edit_limit)
                    or _name_similarity(b.get("vendor_name"),
                                        g.get("trade_name")) >= name_sim_floor):
                candidate = g
                break
        if candidate is None:
            still_unmatched_books.append(b)
            continue
        remaining_g2b.remove(candidate)
        result.exceptions.append({
            "bucket": "gstin_mismatch",
            "books_row": b, "g2b_row": candidate,
            "delta": {
                "gstin_books": b.get("vendor_gstin"),
                "gstin_2b": candidate.get("supplier_gstin"),
                "vendor_books": b.get("vendor_name"),
                "vendor_2b": candidate.get("trade_name"),
            },
        })

    # Step 5 — residue.
    for b in still_unmatched_books:
        result.exceptions.append({
            "bucket": "in_books_not_2b",
            "books_row": b, "g2b_row": None,
            "delta": {"itc_not_yet_available": _tax(b)},
        })
    for g in remaining_g2b:
        result.exceptions.append({
            "bucket": "in_2b_not_books",
            "books_row": None, "g2b_row": g,
            "delta": {"credit_unclaimed": _tax(g)},
        })

    buckets = {name: 0 for name in BUCKETS}
    for e in result.exceptions:
        buckets[e["bucket"]] += 1

    itc_at_risk = round(
        sum(e["delta"].get("itc_not_yet_available", 0.0)
            for e in result.exceptions if e["bucket"] == "in_books_not_2b")
        + sum(abs(e["delta"].get("delta_tax", 0.0))
              for e in result.exceptions if e["bucket"] == "amount_mismatch"),
        2)

    result.stats = {
        "books_rows": len(books_rows),
        "gstr2b_rows": len(g2b_rows),
        # "exact" excludes matches that only resolved via an amendment — those
        # are counted separately so the demo can narrate them.
        "exact_matches": sum(1 for m in result.matches
                             if m["match_type"] == "exact"),
        "amendment_resolved": amendment_resolved,
        "amendments_superseded": superseded,
        "fuzzy_matches": buckets["gstin_mismatch"],
        "exceptions_total": len(result.exceptions),
        "buckets": buckets,
        "itc_at_risk": itc_at_risk,
        "checks": checks,
    }
    return result
