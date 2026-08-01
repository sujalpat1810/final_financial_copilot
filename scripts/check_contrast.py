"""
Verify the design tokens meet WCAG 2.1 AA.

Colour values are read out of frontend/css/tokens.css rather than duplicated
here, so editing a token cannot silently invalidate the claim.  The PAIRS table
records which colour sits on which background — that is the part a checker
cannot infer, and getting it wrong is how "we checked contrast" ends up
measuring text against a white it never appears on.

Run directly for a report:
    python -m scripts.check_contrast

tests/test_contrast.py asserts the same thing, so `pytest` covers it too.
"""

from __future__ import annotations

import re
from pathlib import Path

TOKENS_CSS = Path(__file__).resolve().parent.parent / "frontend" / "css" / "tokens.css"

AA_BODY = 4.5      # normal-size text
AA_LARGE = 3.0     # >=18.66px bold or >=24px, and non-text UI boundaries

# (foreground token, background token, kind). kind picks the threshold.
PAIRS: list[tuple[str, str, str]] = [
    # Body and metadata, on both surfaces they actually appear on.
    ("ink-1", "surface", "body"),
    ("ink-2", "surface", "body"),
    ("ink-3", "surface", "body"),
    ("ink-1", "surface-sunk", "body"),
    ("ink-2", "surface-sunk", "body"),
    ("ink-3", "surface-sunk", "body"),   # the tightest case in the whole palette
    ("ink-2", "bg", "body"),
    ("ink-3", "bg", "body"),

    # Accent, including on its own wash (the provenance chip on hover).
    ("accent", "surface", "body"),
    ("accent", "accent-wash", "body"),
    ("accent", "surface-accent", "body"),

    # Confidence badges, each on its own wash — this is what binds, not white.
    ("ok", "ok-wash", "body"),
    ("warn", "warn-wash", "body"),
    ("mute", "mute-wash", "body"),
    ("stop", "surface", "body"),
    ("stop", "stop-wash", "body"),

    # Interactive control boundaries: WCAG 1.4.11 non-text contrast, 3:1.
    # These are the borders on inputs, buttons, the drop zone and the
    # provenance chip — anything whose edge tells you it is a control.
    ("line-control", "surface", "large"),
    ("line-control", "surface-sunk", "large"),
    ("line-control", "bg", "large"),

    # The relevance bar against its track conveys information, so 3:1 applies.
    ("accent", "line", "large"),

    # Reported but not enforced. --line and --line-strong are decorative
    # separators only — card edges, table rules, the relevance track. 1.4.11
    # exempts them, and darkening them to 3:1 would turn every hairline rule in
    # a dense layout into a heavy grid. They are listed so the numbers stay
    # visible rather than the pair being quietly dropped from the table.
    ("line-strong", "surface", "decorative"),
    ("line", "surface", "decorative"),
]

_COLOUR_RE = re.compile(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;")
_ANY_TOKEN_RE = re.compile(r"^\s*--([a-z0-9-]+)\s*:", re.M)


def read_tokens(path: Path = TOKENS_CSS) -> dict[str, str]:
    """Colour tokens only — the contrast checks need hex values."""
    return dict(_COLOUR_RE.findall(path.read_text(encoding="utf-8")))


def declared_tokens(path: Path = TOKENS_CSS) -> set[str]:
    """
    Every declared custom property, colour or not.

    Separate from read_tokens because the type scale, spacing and layout tokens
    have no hex value — a checker that only saw colours would report the rest as
    undefined.
    """
    return set(_ANY_TOKEN_RE.findall(path.read_text(encoding="utf-8")))


def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def check(tokens: dict[str, str] | None = None) -> list[tuple[str, str, float, float, bool]]:
    """Returns (fg, bg, ratio, required, passed) for every declared pair."""
    tokens = tokens or read_tokens()
    results = []
    for fg, bg, kind in PAIRS:
        missing = [n for n in (fg, bg) if n not in tokens]
        if missing:
            raise KeyError(f"tokens.css is missing --{' and --'.join(missing)}")
        if kind == "decorative":
            required = 0.0     # reported, not enforced — see the note in PAIRS
        else:
            required = AA_BODY if kind == "body" else AA_LARGE
        ratio = contrast_ratio(tokens[fg], tokens[bg])
        results.append((fg, bg, ratio, required, ratio >= required))
    return results


def main() -> int:
    tokens = read_tokens()
    results = check(tokens)
    width = max(len(f"{fg} on {bg}") for fg, bg, *_ in results)
    print(f"{'pair'.ljust(width)}  {'ratio':>6}  {'needs':>5}")
    for fg, bg, ratio, required, ok in results:
        if required == 0.0:
            print(f"{f'{fg} on {bg}'.ljust(width)}  {ratio:>6.2f}      -  decorative")
            continue
        mark = "ok  " if ok else "FAIL"
        print(f"{f'{fg} on {bg}'.ljust(width)}  {ratio:>6.2f}  {required:>5.1f}  {mark}")

    failures = [r for r in results if not r[4]]
    print(f"\n{len(results) - len(failures)}/{len(results)} pass")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
