"""
Download the self-hosted font subsets into frontend/vendor/fonts/.

The UI previously loaded Inter and JetBrains Mono from fonts.googleapis.com.
That is a third-party request on every page load, which contradicts the on-prem
confidentiality story, fails on an air-gapped machine, and blocks first render
over a bad meeting-room connection.  These are latin subsets, ~148 KB total,
committed to the repo so a fresh clone needs no network.

Run only when refreshing them:
    python -m scripts.fetch_fonts
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parent.parent / "frontend" / "vendor" / "fonts"

# Fontsource mirrors the upstream families under the OFL. Weights match those
# actually used in the CSS: any weight declared but not downloaded silently
# falls back to a synthesised face.
FACES = [
    ("inter", 400),
    ("inter", 500),
    ("inter", 600),
    ("inter", 700),
    ("jetbrains-mono", 400),
    ("jetbrains-mono", 600),
]

URL = "https://cdn.jsdelivr.net/fontsource/fonts/{family}@latest/latin-{weight}-normal.woff2"


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    failed = []

    for family, weight in FACES:
        out = DEST / f"{family}-{weight}.woff2"
        url = URL.format(family=family, weight=weight)
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  FAIL {out.name}: {e}")
            failed.append(out.name)
            continue

        # A truncated or error-page response would otherwise be written as a
        # "font" and fail silently at render time.
        if len(data) < 5_000 or data[:4] != b"wOF2":
            print(f"  FAIL {out.name}: not a woff2 file ({len(data)} bytes)")
            failed.append(out.name)
            continue

        out.write_bytes(data)
        print(f"  {len(data):>7,}b  {out.name}")

    total = sum(f.stat().st_size for f in DEST.glob("*.woff2"))
    print(f"\n{total / 1024:.0f} KB in {DEST}")
    if failed:
        print(f"{len(failed)} face(s) failed; the CSS fallback stack will be used for them.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
