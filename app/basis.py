"""
Standalone-vs-consolidated basis detection.

Why this exists
───────────────
Every Indian annual report contains BOTH standalone and consolidated financial
statements, and the figures differ materially — Infosys FY2024-25 reports
₹1,62,990 crore consolidated and ₹1,36,592 crore standalone.  So "What was
Infosys revenue in FY2025?" has two correct answers inside a single document.
Serving one without saying which is the failure mode this product exists to
prevent, so every chunk carries the basis of the section it came from.

Why section structure and not keyword matching
──────────────────────────────────────────────
Per-page keyword matching demonstrably fails.  Page 262 of infosys-ar-25.pdf
sits inside the standalone block, but its prose contains the phrase
"consolidated financial statements" — it is a segment-reporting note referring
across sections.  Any pattern loose enough to match that prose labels a page of
standalone figures as consolidated, which is worse than labelling it unknown.

So markers are matched only against publisher divider / running-header strings:
anchored to the start of a complete short line near the top of the page, and
each carrying the suffix that distinguishes a heading from running prose.

The algorithm: carry forward, terminate
───────────────────────────────────────
State starts undetermined, a marker sets it, and it carries forward until a
terminator clears it.  That is all.  There is no per-publisher branch, because
carry-forward handles both marker shapes without knowing which it is looking at:

  Infosys  names the basis once on a divider page and never repeats it, so the
           carry does the work across ~83 unmarked pages.

  TCS      repeats the basis in a running header, so markers simply re-assert
           the same state each page — harmless — and the transition at the
           standalone divider flips it.  TCS also puts consolidated FIRST, so
           block order is never assumed.

Carry-forward also absorbs an extraction quirk that defeated earlier designs:
pdfplumber reads TCS as a two-page spread and emits the running header on only
one page of each pair (70 markers on odd pages, where pypdf finds 140).  Under
carry-forward the unmarked page simply inherits its neighbour's state.

Markers are strict, terminators may be loose
────────────────────────────────────────────
These two pattern sets are deliberately held to different standards, because
their failure modes are not symmetric:

  a false MARKER assigns the WRONG basis — it says "standalone" about a
  consolidated figure, which actively misleads;

  a false TERMINATOR only clears the state, so pages fall back to undetermined,
  which qualifies the answer downstream.

Failing to `None` is safe; being confidently wrong is not.  That asymmetry is
what licenses "Dear Member" and "Glossary" below as terminators while the bare
phrase "consolidated financial statements" is rejected as a marker.

Verified against measured ground truth — every boundary reproduced exactly under
BOTH extractors, which matters because ingestion.py prefers pdfplumber and falls
back to pypdf.  See tests/test_basis_detection.py.

    infosys-ar-25.pdf            369 pp   standalone 181-263   consolidated 264-346
    infosys-ar-26.pdf            383 pp   standalone 194-274   consolidated 275-356
    annual-report-2024-2025.pdf  336 pp   standalone 241-310   consolidated 171-240

Adding a publisher means adding a pattern here AND a measured fixture to the
test.  This is a pattern registry, not a general solution.
"""

from __future__ import annotations

import re

STANDALONE = "standalone"
CONSOLIDATED = "consolidated"


# ── Markers: strict. A false positive assigns the wrong basis. ────────────────
#
# Do NOT relax these to the bare phrase "consolidated financial statements".
# That is exactly what mislabels infosys-ar-25.pdf p262, and there is a test
# named for it.
_MARKERS = [
    # Infosys divider page. The real line wraps and continues past the phrase
    # ("... Accounting Standards (Ind AS) for the"), so this is a prefix match
    # rather than an anchored full-line one — an earlier version anchored the end
    # and silently matched nothing at all.
    re.compile(
        r"^(standalone|consolidated)\s+financial\s+statements\s+under\s+"
        r"indian\s+accounting\s+standards",
        re.I,
    ),
    # TCS running header, e.g. "Consolidated Financial Statements 2024-25".
    # pdfplumber prepends the printed page number to it ("169 Consolidated
    # Financial Statements 2024-25"), hence the optional leading digits.
    # The fiscal-year suffix is what keeps this from matching the bare phrase.
    re.compile(
        r"^(?:\d{1,4}\s+)?(standalone|consolidated)\s+financial\s+statements\s+"
        r"\d{4}-\d{2}\s*$",
        re.I,
    ),
    # TCS notes header, e.g. "Notes forming part of the Consolidated Financial
    # Statements".
    re.compile(
        r"^notes?\s+forming\s+part\s+of\s+the\s+"
        r"(standalone|consolidated)\s+financial\s+statements",
        re.I,
    ),
]


# ── Terminators: may be loose. A false positive only clears the state. ───────
#
# Each entry marks the start of a section that is definitely not financial
# statements. Firing early is harmless (state is already None); firing late
# would leave a stale basis on non-statement pages.
_TERMINATORS = [
    # Infosys AGM notice heading. pypdf finds it as a running header on the
    # notice's first page; pdfplumber drops that header, hence the next pattern.
    re.compile(r"^notice\s+of\s+the\s+\d+\s*(?:st|nd|rd|th)\s+annual\s+general\s+meeting", re.I),
    # The AGM cover letter's salutation. This is the only signal pdfplumber
    # surfaces on that page, and without it the consolidated block over-runs by
    # one page. Anchored, so the phrase mid-sentence in a dividend note cannot
    # fire it — infosys-ar-26 p323 is the case that rules out a loose match on
    # "annual general meeting".
    re.compile(r"^dear\s+members?\b", re.I),
    # TCS ends its financial statements with the glossary; it has no AGM notice
    # at the back (its notice sits near the front, well before any marker).
    re.compile(r"^glossary\s*$", re.I),
    re.compile(r"^abbreviations\s*$", re.I),
]


# A section heading or running header is short; prose that happens to begin with
# the same words is not. 130 because the real Infosys divider line is 82 chars —
# an earlier 80-char guard skipped it.
_MAX_LINE_CHARS = 130

# Headings and running headers sit at the top of the page. Scanning the whole
# page would readmit the prose false positives these patterns exist to exclude.
_HEAD_LINES = 8


def _candidate_lines(page_text: str) -> list[str]:
    """Short non-empty lines from the top of a page — the only text considered."""
    lines = [ln.strip() for ln in (page_text or "").split("\n") if ln.strip()]
    return [ln for ln in lines[:_HEAD_LINES] if len(ln) <= _MAX_LINE_CHARS]


def find_markers(pages: list[str]) -> tuple[dict[int, str], set[int]]:
    """
    Scan pages (1-indexed) for basis markers and block terminators.

    Returns ({page_number: "standalone"|"consolidated"}, {terminator pages}).
    The first marker on a page wins, so a page is never given two bases.
    """
    explicit: dict[int, str] = {}
    terminators: set[int] = set()

    for page_no, text in enumerate(pages, start=1):
        for line in _candidate_lines(text):
            if any(t.match(line) for t in _TERMINATORS):
                terminators.add(page_no)
            if page_no not in explicit:
                for pattern in _MARKERS:
                    match = pattern.match(line)
                    if match:
                        explicit[page_no] = match.group(1).lower()
                        break

    return explicit, terminators


def assign_basis(pages: list[str]) -> list[str | None]:
    """
    Label every page "standalone", "consolidated", or None (undetermined).

    `pages` is the extracted text per page in document order; the returned list is
    parallel to it, so a caller's page number is index + 1.

    A marker on the same page as a terminator wins — the terminator is applied
    first, so a divider page that also carries a stale running header still opens
    its block correctly.
    """
    explicit, terminators = find_markers(pages)

    out: list[str | None] = []
    state: str | None = None
    for page_no in range(1, len(pages) + 1):
        if page_no in terminators:
            state = None
        if page_no in explicit:
            state = explicit[page_no]
        out.append(state)
    return out
