"""
Tests for app/verify.py — the deterministic checks behind the UI's
verification badges.

The GSTIN cases are pinned against the dataset generator: the generator plants
valid and invalid numbers deliberately, so verifier and generator sharing one
implementation is load-bearing, and these tests are what notice if that stops
being true.
"""

import pytest

from app.verify import (
    gstin_check_char,
    gstin_checksum_valid,
    lines_sum_to_total,
    rate_in_slabs,
    tax_split_consistent,
)


# ── GSTIN checksum ────────────────────────────────────────────────────────────

def test_check_char_is_deterministic_and_in_charset():
    c = gstin_check_char("27AAECM1201F1Z")
    assert c == gstin_check_char("27AAECM1201F1Z")
    assert c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def test_generated_gstins_validate():
    """Round trip: a check char computed here must validate here."""
    for prefix in ("27AAECM1201F1Z", "07AAECS4521K1Z", "24AAECV7031N2Z"):
        assert gstin_checksum_valid(prefix + gstin_check_char(prefix))


def test_single_character_change_breaks_the_checksum():
    good = "27AAECM1201F1Z" + gstin_check_char("27AAECM1201F1Z")
    for i in range(14):
        replacement = "0" if good[i] != "0" else "1"
        bad = good[:i] + replacement + good[i + 1:]
        # Some substitutions also break the SHAPE (letters where digits go),
        # which is an equally valid reason to reject — either way, invalid.
        assert not gstin_checksum_valid(bad), f"position {i} change not caught"


def test_shape_failures_return_false_not_raise():
    for bad in ("", None, "27AAECM1201F1", "27AAECM1201F1ZZZ",
                "27aaecm1201f1zz"[:14], "NOT A GSTIN AT ALL"):
        assert gstin_checksum_valid(bad) is False


def test_lowercase_input_is_normalised():
    good = "27AAECM1201F1Z" + gstin_check_char("27AAECM1201F1Z")
    assert gstin_checksum_valid(good.lower())


def test_dataset_plants_match_the_verifier():
    """The generator's planted valid/invalid GSTINs agree with this verifier."""
    import csv
    from pathlib import Path

    g2b = Path("data/ca_dataset/mehta_gstr2b_2025-12.csv")
    if not g2b.exists():
        pytest.skip("dataset not generated")
    rows = list(csv.DictReader(open(g2b, encoding="utf-8")))
    bad = [r["invoice_no"] for r in rows
           if not gstin_checksum_valid(r["supplier_gstin"])]
    assert bad == ["MPK/2025/0740"]


# ── Tax split ─────────────────────────────────────────────────────────────────

def test_intra_state_split_is_consistent():
    assert tax_split_consistent(cgst=900.0, sgst=900.0, igst=0.0)


def test_inter_state_split_is_consistent():
    assert tax_split_consistent(cgst=0.0, sgst=0.0, igst=1800.0)


def test_mixed_split_is_inconsistent():
    assert not tax_split_consistent(cgst=900.0, sgst=0.0, igst=900.0)


def test_unequal_halves_are_inconsistent():
    assert not tax_split_consistent(cgst=900.0, sgst=800.0, igst=0.0)


def test_zero_tax_row_is_consistent():
    """A nil-rated supply has no tax at all — that is not an inconsistency."""
    assert tax_split_consistent(cgst=0.0, sgst=0.0, igst=0.0)


# ── Line sums ─────────────────────────────────────────────────────────────────

def test_lines_summing_to_total_pass():
    assert lines_sum_to_total([100.0, 250.5, 49.5], 400.0)


def test_lines_not_summing_fail():
    assert not lines_sum_to_total([100.0, 250.0], 400.0)


def test_rounding_tolerance_of_one_paisa():
    assert lines_sum_to_total([33.333, 33.333, 33.333], 100.0, tol=0.01)


# ── Rate slabs ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rate", [0, 0.25, 3, 5, 12, 18, 28])
def test_gst_slabs_accepted(rate):
    assert rate_in_slabs(rate)


@pytest.mark.parametrize("rate", [7, 10, 15, 33, -5])
def test_non_slab_rates_rejected(rate):
    assert not rate_in_slabs(rate)
