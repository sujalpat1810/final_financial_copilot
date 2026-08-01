"""
Generate tests/fixtures/basis_pages.json from the real annual reports.

Why a fixture at all
────────────────────
tests/test_basis_detection.py asserts that app.basis.assign_basis reproduces
measured section boundaries exactly.  Running that against the real PDFs means
extracting text from ~1,100 pages with pdfplumber, which takes minutes — too
slow for a test that should run on every change.

So this script captures the only input assign_basis actually reads — the first
few lines of each page — and commits it.  The fast test runs against the
fixture; a slow opt-in test re-runs the same assertions against the real PDFs so
a change in text extraction cannot silently invalidate the fixture.

Both extractors are captured because ingestion.py prefers pdfplumber and falls
back to pypdf, and the markers must survive either.

Run:
    .venv/Scripts/python.exe -m scripts.make_basis_fixture
"""

from __future__ import annotations

import json
from pathlib import Path

# Keep in sync with app.basis._HEAD_LINES. Storing the first N lines VERBATIM
# (rather than pre-filtering short ones) matters: app.basis takes lines[:N] and
# THEN drops long ones, so a long line inside the window must stay in the
# fixture to keep occupying its slot.
HEAD_LINES = 8

# Must stay above app.basis._MAX_LINE_CHARS (130) so a truncated long line is
# still recognisably too long and gets filtered the same way.
MAX_LINE_CHARS = 200

DOCS = [
    "pdf_data/infosys-ar-25.pdf",
    "pdf_data/infosys-ar-26.pdf",
    "pdf_data/annual-report-2024-2025.pdf",
]

OUT = Path("tests/fixtures/basis_pages.json")


def _head(text: str) -> list[str]:
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    return [ln[:MAX_LINE_CHARS] for ln in lines[:HEAD_LINES]]


def pypdf_pages(path: str) -> list[str]:
    from pypdf import PdfReader

    out = []
    for page in PdfReader(path).pages:
        try:
            out.append(page.extract_text() or "")
        except Exception:
            out.append("")
    return out


def pdfplumber_pages(path: str) -> list[str]:
    import pdfplumber

    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            try:
                out.append(page.extract_text() or "")
            except Exception:
                out.append("")
    return out


def main() -> None:
    fixture: dict[str, dict] = {}

    for path in DOCS:
        if not Path(path).exists():
            print(f"skip (missing): {path}")
            continue
        name = Path(path).name
        fixture[name] = {}
        for extractor, loader in (("pypdf", pypdf_pages), ("pdfplumber", pdfplumber_pages)):
            print(f"  {name}  {extractor} …", flush=True)
            pages = loader(path)
            fixture[name][extractor] = [_head(t) for t in pages]
            print(f"    {len(pages)} pages", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT} ({kb:.0f} KB)")


if __name__ == "__main__":
    main()
