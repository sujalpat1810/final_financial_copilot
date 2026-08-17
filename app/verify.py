"""
Deterministic verification checks — the demo's trust layer.

Everything in this module is pure Python over plain values: no LLM, no I/O, no
config. That is the point. These checks back the UI's verification badges, and
a badge is only worth showing if a CA could re-derive it by hand. "38/40 GSTINs
pass checksum" is an auditable fact; "87% confident" is a vibe.

Started in feature 02 (the dataset generator needs the GSTIN checksum to plant
valid and invalid numbers deliberately); feature 05 adds the tax-math checks.
"""

from __future__ import annotations

import re

# ── GSTIN ─────────────────────────────────────────────────────────────────────
#
# A GSTIN is 15 characters: 2-digit state code + 10-character PAN + 1 entity
# code + the letter Z + 1 check character.  The check character is a Luhn
# variant over the base-36 alphabet: walking the first 14 characters from the
# RIGHT, character values are multiplied alternately by 2 and 1 (rightmost gets
# 2), each product is digit-summed in base 36 (quotient + remainder), and the
# check character makes the total a multiple of 36.

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_SHAPE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


def gstin_check_char(first14: str) -> str:
    """Compute the 15th (check) character for the first 14 characters."""
    if len(first14) != 14:
        raise ValueError(f"GSTIN prefix must be 14 characters, got {len(first14)}")
    total = 0
    factor = 2
    for ch in reversed(first14.upper()):
        value = _CHARSET.index(ch)
        product = value * factor
        total += product // 36 + product % 36
        factor = 1 if factor == 2 else 2
    return _CHARSET[(36 - total % 36) % 36]


def gstin_checksum_valid(gstin: str) -> bool:
    """
    True only when the GSTIN has the right shape AND its check character
    verifies.  Shape failures return False rather than raising: the caller is
    validating operator-entered or extracted data, and "invalid" is the answer,
    not an exception.
    """
    g = (gstin or "").strip().upper()
    if not _GSTIN_SHAPE.match(g):
        return False
    return gstin_check_char(g[:14]) == g[14]


# ── Tax arithmetic ────────────────────────────────────────────────────────────
#
# GST splits one tax two ways: an intra-state supply carries CGST + SGST in
# equal halves, an inter-state supply carries IGST alone.  A row carrying both
# kinds, or unequal halves, is malformed however it is read.

def tax_split_consistent(cgst: float, sgst: float, igst: float,
                         tol: float = 0.01) -> bool:
    """True when the CGST/SGST/IGST combination is a legal GST split."""
    has_intra = abs(cgst) > tol or abs(sgst) > tol
    has_inter = abs(igst) > tol
    if has_intra and has_inter:
        return False
    if has_intra:
        return abs(cgst - sgst) <= tol
    return True  # inter-state, or a nil-rated row — both fine


def lines_sum_to_total(line_amounts: list[float], total: float,
                       tol: float = 0.01) -> bool:
    """True when the line items add up to the stated total (± rounding)."""
    return abs(sum(line_amounts) - total) <= tol


# The GST rate slabs in force, plus the special rates (0.25% rough diamonds,
# 3% gold, 1.5%/6%/7.5% composition-adjacent).  A rate outside this set on an
# invoice is either an arithmetic error or a made-up number.
_SLABS = (0.0, 0.1, 0.25, 1.0, 1.5, 3.0, 5.0, 6.0, 7.5, 12.0, 18.0, 28.0)


def rate_in_slabs(rate: float, tol: float = 0.01) -> bool:
    """True when the rate is one of the GST slabs."""
    return any(abs(rate - slab) <= tol for slab in _SLABS)
