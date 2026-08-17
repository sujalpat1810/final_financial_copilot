"""
The demo's insurance policy: the reconciliation engine run against the real
committed dataset must reproduce EXACTLY the outcome the generator planted.

EXPECTED is imported from the generator itself, so the engine, the data, and
the demo script cannot drift apart without this file going red.
"""

from pathlib import Path

import pytest

from app.recon import BUCKETS, load_gstr2b, load_register, reconcile
from scripts.make_ca_dataset import EXPECTED

DATASET = Path("data/ca_dataset")
BOOKS = DATASET / f"mehta_purchase_register_{EXPECTED['period']}.csv"
G2B = DATASET / f"mehta_gstr2b_{EXPECTED['period']}.csv"

pytestmark = pytest.mark.skipif(
    not BOOKS.exists(), reason="dataset not generated")


@pytest.fixture(scope="module")
def result():
    return reconcile(load_register(str(BOOKS)), load_gstr2b(str(G2B)))


def test_row_counts_match_the_plant(result):
    assert result.stats["books_rows"] == EXPECTED["books_rows"]
    assert result.stats["gstr2b_rows"] == EXPECTED["gstr2b_rows"]


def test_exact_matches_match_the_plant(result):
    assert result.stats["exact_matches"] == EXPECTED["exact_matches"]


def test_amendment_is_resolved_not_mismatched(result):
    """The B2BA pair must match against the AMENDED value — matching the
    superseded original would invent an amount mismatch the supplier fixed."""
    assert result.stats["amendment_resolved"] == EXPECTED["amendment_resolved"]
    amended = [m for m in result.matches if m["match_type"] == "amended"]
    assert len(amended) == 1
    assert amended[0]["books_row"]["invoice_no"] == \
        EXPECTED["planted"]["amendment_invoice_no"]


def test_every_bucket_count_matches_the_plant(result):
    assert result.stats["buckets"] == EXPECTED["exceptions"]


def test_bucket_names_are_the_closed_set(result):
    assert set(result.stats["buckets"]) == set(BUCKETS)
    for e in result.exceptions:
        assert e["bucket"] in BUCKETS


def test_duplicate_is_the_planted_invoice(result):
    dups = [e for e in result.exceptions if e["bucket"] == "duplicate_in_books"]
    assert len(dups) == 1
    assert dups[0]["books_row"]["invoice_no"] == \
        EXPECTED["planted"]["duplicate_invoice_no"]


def test_unknown_supplier_lands_in_2b_not_books(result):
    only_2b = [e for e in result.exceptions if e["bucket"] == "in_2b_not_books"]
    names = {e["g2b_row"]["trade_name"] for e in only_2b}
    assert EXPECTED["planted"]["unknown_supplier"] in names


def test_itc_at_risk_matches_the_notice(result):
    """The recon's quantified exposure must equal the ASMT-10's figure — the
    demo's recon → notice story depends on these never disagreeing."""
    assert result.stats["itc_at_risk"] == pytest.approx(
        EXPECTED["itc_at_risk"], abs=0.01)


def test_gstin_mismatches_carry_both_identities(result):
    for e in result.exceptions:
        if e["bucket"] == "gstin_mismatch":
            assert e["delta"]["gstin_books"] != e["delta"]["gstin_2b"]
            assert e["books_row"] and e["g2b_row"]


def test_verification_checks_count_the_planted_bad_gstin(result):
    checks = result.stats["checks"]
    # Books GSTINs are all valid; 2B carries exactly one planted checksum
    # failure (the MPK typo).
    assert checks["gstin_valid_books"] == checks["gstin_total_books"]
    assert checks["gstin_total_2b"] - checks["gstin_valid_2b"] == 1


def test_reconcile_is_deterministic(result):
    again = reconcile(load_register(str(BOOKS)), load_gstr2b(str(G2B)))
    assert again.stats == result.stats
    assert [e["bucket"] for e in again.exceptions] == \
        [e["bucket"] for e in result.exceptions]


# ── Unit cases independent of the dataset ─────────────────────────────────────

def _b(inv, gstin="27AAECM1201F1ZL", value=100.0, cgst=9.0, sgst=9.0,
       igst=0.0, vendor="Vendor A"):
    return {"invoice_no": inv, "vendor_gstin": gstin, "vendor_name": vendor,
            "taxable_value": value, "cgst": cgst, "sgst": sgst, "igst": igst}


def _g(inv, gstin="27AAECM1201F1ZL", value=100.0, cgst=9.0, sgst=9.0,
       igst=0.0, trade="VENDOR A", kind="B2B", original=""):
    return {"invoice_no": inv, "supplier_gstin": gstin, "trade_name": trade,
            "taxable_value": value, "cgst": cgst, "sgst": sgst, "igst": igst,
            "doc_kind": kind, "original_invoice_no": original}


def test_amount_within_tolerance_is_a_match():
    r = reconcile([_b("A1", value=100.0)], [_g("A1", value=100.5)],
                  amount_tol=1.0)
    assert r.stats["exact_matches"] == 1
    assert r.stats["exceptions_total"] == 0


def test_amount_beyond_tolerance_is_a_mismatch():
    r = reconcile([_b("A1", value=100.0)], [_g("A1", value=150.0)])
    assert r.stats["buckets"]["amount_mismatch"] == 1
    delta = r.exceptions[0]["delta"]
    assert delta["delta_taxable"] == 50.0


def test_invoice_number_formatting_is_normalised():
    """ACM/2025/1041 in books must match ACM-2025-1041 in 2B."""
    r = reconcile([_b("ACM/2025/1041")], [_g("ACM-2025-1041")])
    assert r.stats["exact_matches"] == 1


def test_empty_inputs_produce_empty_result():
    r = reconcile([], [])
    assert r.stats["exceptions_total"] == 0
    assert r.stats["exact_matches"] == 0
