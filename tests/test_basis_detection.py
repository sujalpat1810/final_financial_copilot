"""
Basis detection is a decision boundary, so it gets measured ground truth.

The BOUNDARIES table below was measured by hand from the actual PDFs (PDF page
numbers, not printed page numbers).  These are FIXTURES, not configuration:
app.basis derives the ranges from the documents' own headings, and these tests
assert it reproduces them exactly.  If detection drifts, this fails.

Two tiers:
  * fast (default) — runs against tests/fixtures/basis_pages.json, which holds
    the head lines of every page for both extractors. Milliseconds.
  * slow (-m slow) — re-runs the same assertions against the real PDFs, so a
    change in text extraction cannot silently invalidate the fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.basis import CONSOLIDATED, STANDALONE, assign_basis, find_markers

FIXTURE = Path(__file__).parent / "fixtures" / "basis_pages.json"
PDF_DIR = Path(__file__).parent.parent / "pdf_data"

# doc -> (page count, standalone range, consolidated range)
BOUNDARIES = {
    "infosys-ar-25.pdf": (369, (181, 263), (264, 346)),
    "infosys-ar-26.pdf": (383, (194, 274), (275, 356)),
    "annual-report-2024-2025.pdf": (336, (241, 310), (171, 240)),
}

EXTRACTORS = ("pypdf", "pdfplumber")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_fixture() -> dict:
    if not FIXTURE.exists():
        pytest.skip(f"{FIXTURE} missing — run scripts/make_basis_fixture.py")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _pages_from_fixture(fixture: dict, doc: str, extractor: str) -> list[str]:
    if doc not in fixture or extractor not in fixture[doc]:
        pytest.skip(f"{doc}/{extractor} not in fixture")
    # assign_basis reads page text; the fixture stores each page's head lines
    # verbatim, so rejoining reproduces exactly what the detector would see.
    return ["\n".join(lines) for lines in fixture[doc][extractor]]


def _span(basis: list[str | None], value: str) -> tuple[tuple[int, int] | None, int]:
    pages = [i + 1 for i, b in enumerate(basis) if b == value]
    return ((pages[0], pages[-1]), len(pages)) if pages else (None, 0)


def _assert_boundaries(doc: str, pages: list[str]) -> None:
    page_count, expect_sa, expect_co = BOUNDARIES[doc]
    assert len(pages) == page_count, f"{doc}: page count"

    basis = assign_basis(pages)
    sa_span, sa_n = _span(basis, STANDALONE)
    co_span, co_n = _span(basis, CONSOLIDATED)

    assert sa_span == expect_sa, f"{doc}: standalone span"
    assert co_span == expect_co, f"{doc}: consolidated span"

    # Contiguity: a span that matched its endpoints could still have holes.
    assert sa_n == expect_sa[1] - expect_sa[0] + 1, f"{doc}: standalone has gaps"
    assert co_n == expect_co[1] - expect_co[0] + 1, f"{doc}: consolidated has gaps"


# ── Fast tier ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("doc", sorted(BOUNDARIES))
@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_boundaries_reproduced(doc: str, extractor: str) -> None:
    """Measured ranges are derived exactly, from either extractor."""
    _assert_boundaries(doc, _pages_from_fixture(_load_fixture(), doc, extractor))


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_prose_mentioning_other_basis_does_not_flip_page(extractor: str) -> None:
    """
    THE regression test for the marker patterns.

    infosys-ar-25.pdf p262 is inside the standalone block but its text contains
    "consolidated financial statements" — a segment-reporting note referring
    across sections. Any pattern loose enough to match that prose labels a page
    of standalone figures as consolidated. If this fails, someone relaxed the
    patterns in app.basis.
    """
    pages = _pages_from_fixture(_load_fixture(), "infosys-ar-25.pdf", extractor)
    assert assign_basis(pages)[261] == STANDALONE


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_consolidated_may_precede_standalone(extractor: str) -> None:
    """TCS puts consolidated first. Block order must never be assumed."""
    pages = _pages_from_fixture(_load_fixture(), "annual-report-2024-2025.pdf", extractor)
    basis = assign_basis(pages)
    co_start = _span(basis, CONSOLIDATED)[0][0]
    sa_start = _span(basis, STANDALONE)[0][0]
    assert co_start < sa_start


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_glossary_terminates_tcs_standalone_block(extractor: str) -> None:
    """
    TCS has no AGM notice at the back — its notice sits near the front, before any
    marker. The standalone block is ended by the glossary at p311. Without that
    terminator standalone runs to the last page instead of 310.
    """
    pages = _pages_from_fixture(_load_fixture(), "annual-report-2024-2025.pdf", extractor)
    basis = assign_basis(pages)
    for page_no in (311, 320, 336):
        assert basis[page_no - 1] is None, f"p{page_no} should be undetermined"


@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_carry_bridges_gaps_in_running_headers(extractor: str) -> None:
    """
    pdfplumber reads TCS as a two-page spread and emits the running header on only
    one page of each pair — 70 markers where pypdf finds 140. Carry-forward must
    give the unmarked page its neighbour's basis, or half the block goes unlabelled.
    """
    pages = _pages_from_fixture(_load_fixture(), "annual-report-2024-2025.pdf", extractor)
    explicit, _ = find_markers(pages)
    basis = assign_basis(pages)
    unmarked_inside_block = [p for p in range(171, 241) if p not in explicit]
    if extractor == "pdfplumber":
        assert unmarked_inside_block, "expected gaps under pdfplumber"
    for page_no in unmarked_inside_block:
        assert basis[page_no - 1] == CONSOLIDATED, f"p{page_no} lost its basis to a gap"


@pytest.mark.parametrize("doc", ["infosys-ar-25.pdf", "infosys-ar-26.pdf"])
@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_terminator_ends_carried_block(doc: str, extractor: str) -> None:
    """
    Infosys names the basis once on a divider page, so state carries ~83 pages.
    Without a terminator the AGM notice inherits a basis from that far back.

    This is also the extractor-divergence test: pypdf finds the AGM running header
    on the notice's first page, pdfplumber drops it and is caught only by the
    "Dear Member" salutation. Both must land on the same block end.
    """
    pages = _pages_from_fixture(_load_fixture(), doc, extractor)
    basis = assign_basis(pages)
    last_consolidated = BOUNDARIES[doc][2][1]
    assert basis[last_consolidated] is None, "page after the block must be undetermined"
    assert basis[-1] is None, "last page must be undetermined"


@pytest.mark.parametrize("doc", sorted(BOUNDARIES))
@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_no_terminator_falls_inside_a_real_block(doc: str, extractor: str) -> None:
    """
    Terminators are allowed to be loose, but one landing inside a statements block
    would punch a hole in it and silently downgrade real figures to undetermined.
    """
    pages = _pages_from_fixture(_load_fixture(), doc, extractor)
    _, terminators = find_markers(pages)
    _, expect_sa, expect_co = BOUNDARIES[doc]
    lo, hi = min(expect_sa[0], expect_co[0]), max(expect_sa[1], expect_co[1])
    assert [t for t in terminators if lo <= t <= hi] == []


@pytest.mark.parametrize("doc", sorted(BOUNDARIES))
@pytest.mark.parametrize("extractor", EXTRACTORS)
def test_narrative_pages_are_undetermined(doc: str, extractor: str) -> None:
    """
    MD&A, ESG and the board report genuinely have no basis. None is the honest
    value there, not a gap to be filled.
    """
    basis = assign_basis(_pages_from_fixture(_load_fixture(), doc, extractor))
    first_marked = min(BOUNDARIES[doc][1][0], BOUNDARIES[doc][2][0])
    assert all(b is None for b in basis[: first_marked - 1])


# ── Unit-level behaviour, no fixture needed ───────────────────────────────────

def test_empty_and_blank_input() -> None:
    assert assign_basis([]) == []
    assert assign_basis(["", "   ", "\n\n"]) == [None, None, None]


def test_bare_phrase_is_not_a_marker() -> None:
    """
    "Consolidated Financial Statements" with no distinguishing suffix is not
    accepted. This is what keeps prose and contents pages from flipping state.
    """
    assert assign_basis(["Consolidated Financial Statements"]) == [None]
    assert assign_basis(["The consolidated financial statements of the Group"]) == [None]


def test_marker_must_start_the_line() -> None:
    pages = ["See the Consolidated Financial Statements 2024-25 for details"]
    assert assign_basis(pages) == [None]


def test_long_line_is_not_treated_as_a_heading() -> None:
    line = "Consolidated Financial Statements 2024-25 " + ("x" * 140)
    assert assign_basis([line]) == [None]


def test_marker_below_the_head_window_is_ignored() -> None:
    """A basis heading buried in body text is prose, not a section marker."""
    pages = ["\n".join(["filler"] * 12 + ["Consolidated Financial Statements 2024-25"])]
    assert assign_basis(pages) == [None]


def test_divider_carries_until_terminator() -> None:
    pages = [
        "cover",
        "Standalone Financial Statements under Indian Accounting Standards (Ind AS)",
        "balance sheet",
        "notes",
        "Notice of the 44th Annual General Meeting",
        "resolutions",
    ]
    assert assign_basis(pages) == [None, STANDALONE, STANDALONE, STANDALONE, None, None]


def test_repeated_markers_reassert_the_same_state() -> None:
    """Running headers just re-set the state they already hold — harmless."""
    pages = [
        "narrative",
        "Consolidated Financial Statements 2024-25",
        "Consolidated Financial Statements 2024-25",
        "Glossary",
    ]
    assert assign_basis(pages) == [None, CONSOLIDATED, CONSOLIDATED, None]


def test_page_number_prefixed_running_header_is_matched() -> None:
    """pdfplumber emits TCS headers as '169 Consolidated Financial Statements 2024-25'."""
    assert assign_basis(["169 Consolidated Financial Statements 2024-25"]) == [CONSOLIDATED]


def test_marker_on_a_terminator_page_still_opens_its_block() -> None:
    """Terminator is applied before the marker, so the marker wins on that page."""
    pages = ["Glossary\nStandalone Financial Statements 2024-25", "balance sheet"]
    assert assign_basis(pages) == [STANDALONE, STANDALONE]


def test_agm_phrase_in_prose_does_not_terminate() -> None:
    """
    infosys-ar-26 p323 is a dividend note reading "...approval of shareholders in
    the Annual General Meeting...". Terminators are line-anchored so it cannot fire.
    """
    pages = [
        "Consolidated Financial Statements under Indian Accounting Standards",
        "The payment is subject to the approval of shareholders in the Annual General Meeting",
        "Revenue 100",
    ]
    assert assign_basis(pages) == [CONSOLIDATED, CONSOLIDATED, CONSOLIDATED]


# ── Slow tier — same assertions, real PDFs ────────────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("doc", sorted(BOUNDARIES))
def test_boundaries_against_real_pdfs(doc: str) -> None:
    """Guards against the fixture going stale relative to the PDFs."""
    path = PDF_DIR / doc
    if not path.exists():
        pytest.skip(f"{path} not present")
    pdfplumber = pytest.importorskip("pdfplumber")

    pages = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
    _assert_boundaries(doc, pages)
