"""
Frontend structural guarantees, checked without a browser.

These are the claims that are easy to make and easy to break silently: AA
contrast, no external requests, no emoji icons, no vendor name in the chrome,
and every asset the page references actually existing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from scripts.check_contrast import check, declared_tokens

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
INDEX = FRONTEND / "index.html"
CSS_DIR = FRONTEND / "css"
CSS_FILES = ["tokens.css", "base.css", "layout.css", "components.css"]


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX.read_text(encoding="utf-8")


# ── Contrast ──────────────────────────────────────────────────────────────────

def test_every_enforced_token_pair_meets_wcag_aa():
    """
    Acceptance criterion 6, as a test rather than a promise. Pairs are declared
    in scripts/check_contrast.PAIRS against the background each colour actually
    sits on — measuring everything against white is how this gets faked.
    """
    failures = [
        f"--{fg} on --{bg}: {ratio:.2f} < {required}"
        for fg, bg, ratio, required, ok in check()
        if not ok
    ]
    assert not failures, "contrast failures:\n  " + "\n  ".join(failures)


def test_tokens_referenced_by_css_are_all_defined():
    """A typo'd var() silently renders as nothing, which is hard to spot."""
    tokens = declared_tokens()
    used: set[str] = set()
    for name in CSS_FILES:
        used |= set(re.findall(r"var\(--([a-z0-9-]+)\)", (CSS_DIR / name).read_text(encoding="utf-8")))
    assert not (used - tokens), f"undefined tokens: {sorted(used - tokens)}"


# ── No external requests ──────────────────────────────────────────────────────

def test_page_makes_no_external_requests(index_html):
    """
    Zero third-party requests is part of the on-prem positioning, and an
    air-gapped machine must render identically. Fonts are self-hosted, icons are
    an inline sprite, PDF.js is vendored.
    """
    for pattern in ("//fonts.googleapis.com", "//fonts.gstatic.com",
                    "//cdn.", "//unpkg.com", "//cdnjs."):
        assert pattern not in index_html, f"external reference: {pattern}"


def test_no_external_urls_in_css():
    for name in CSS_FILES:
        css = (CSS_DIR / name).read_text(encoding="utf-8")
        for url in re.findall(r"url\(([^)]+)\)", css):
            assert not url.strip("'\"").startswith(("http:", "https:", "//")), url


def test_referenced_local_assets_exist(index_html):
    """A 404'd stylesheet or font shows up as an unstyled page, not an error."""
    refs = re.findall(r'(?:href|src)="(?!https?:|//|#)([^"]+)"', index_html)
    assert refs
    for ref in refs:
        assert (FRONTEND / ref).exists(), f"missing asset referenced by index.html: {ref}"


def test_font_faces_resolve_to_real_files():
    css = (FRONTEND / "vendor" / "fonts" / "fonts.css").read_text(encoding="utf-8")
    faces = re.findall(r"url\('([^']+)'\)", css)
    assert len(faces) >= 4
    for face in faces:
        path = FRONTEND / "vendor" / "fonts" / face
        assert path.exists(), f"missing font file: {face}"
        # woff2 magic — a truncated download or an error page would otherwise be
        # served as a font and fail only at render time.
        assert path.read_bytes()[:4] == b"wOF2", f"{face} is not a woff2 file"


# ── Icons and chrome ──────────────────────────────────────────────────────────

def test_no_emoji_icons(index_html):
    """
    Emoji render inconsistently across platforms and read as informal. The old UI
    used a dozen of them; they are replaced by a Lucide sprite.
    """
    emoji = re.findall(
        r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF✨⚡⭐]",
        index_html,
    )
    assert not emoji, f"emoji found in markup: {emoji}"


def test_icon_sprite_defines_every_referenced_glyph(index_html):
    defined = set(re.findall(r'<g id="(i-[a-z0-9-]+)"', index_html))
    used = set(re.findall(r'<use href="#(i-[a-z0-9-]+)"', index_html))
    assert used, "no icons referenced"
    assert not (used - defined), f"undefined icons: {sorted(used - defined)}"


def test_no_vendor_name_in_the_chrome(index_html):
    """Acceptance criterion 5. The model name belongs in the About panel."""
    lowered = index_html.lower()
    for word in ("gemini", "google", "genai", "openai", "gpt", "claude"):
        assert word not in lowered, f"{word!r} appears in the frontend markup"


def test_no_gradient_text_or_glows():
    """Both were specific complaints about the old UI; keep them out."""
    css = "\n".join((CSS_DIR / n).read_text(encoding="utf-8") for n in CSS_FILES)
    assert "background-clip:text" not in css.replace(" ", "")
    assert "-webkit-background-clip" not in css
    # Glows are box-shadows with no offset and a large blur; real shadows here
    # are small and offset. Check for the neon pattern specifically.
    assert not re.search(r"box-shadow:\s*0\s+0\s+\d{2,}px", css)


def test_no_chat_bubble_styling():
    """The chat metaphor is the thing this refactor is removing."""
    css = "\n".join((CSS_DIR / n).read_text(encoding="utf-8") for n in CSS_FILES)
    assert ".bubble" not in css
    assert "border-radius:18px 18px 4px 18px" not in css


# ── Structure the JS depends on ───────────────────────────────────────────────

@pytest.mark.parametrize("element_id", [
    "healthDot", "healthText", "statDocs", "statChunks",
    "dropZone", "fileInput", "entityInput", "fyInput", "docNameInput",
    "uploadProgress", "progressFill", "progressLabel",
    "docList", "stream", "streamInner", "welcome",
    "seeds", "queryInput", "askBtn",
    "panel", "panelDoc", "panelPage", "panelBody", "panelExcerpt",
    "panelClose", "panelDownload", "panelHlState",
    "toasts", "aboutBtn",
])
def test_required_element_is_present(index_html, element_id):
    """main.js addresses these by id; a rename would fail only at runtime."""
    assert f'id="{element_id}"' in index_html


def test_entity_and_fiscal_year_are_marked_required(index_html):
    """They are not detected from the document, so the form must demand them."""
    for field in ("entityInput", "fyInput"):
        block = index_html[index_html.index(f'id="{field}"') - 200:]
        assert "required" in block[:300]


def test_markup_carries_no_inline_style_or_script_blocks(index_html):
    """
    The point of the split. A little inline `style=` on layout spacers is fine;
    a <style> or <script> block means CSS or logic leaked back into the markup.
    """
    assert "<style" not in index_html
    scripts = re.findall(r"<script[^>]*>", index_html)
    assert scripts == ['<script type="module" src="js/main.js">']


# ── Serving ───────────────────────────────────────────────────────────────────

def test_frontend_is_mounted_without_shadowing_the_api():
    """
    StaticFiles at "/" would swallow /query and /documents. Mounted at /app and
    registered last, so every API route still resolves.
    """
    client = TestClient(main.app)

    assert client.get("/health").status_code == 200
    assert client.get("/documents").status_code == 200

    index = client.get("/app/")
    assert index.status_code == 200
    assert "Financial Copilot" in index.text

    for name in CSS_FILES:
        assert client.get(f"/app/css/{name}").status_code == 200
    assert client.get("/app/js/main.js").status_code == 200


def test_every_css_class_used_by_the_app_is_defined():
    """
    A class name in a template literal that has no rule renders as unstyled
    markup — no error, no warning, just a card that quietly looks wrong. This is
    the CSS twin of test_tokens_referenced_by_css_are_all_defined.
    """
    css = "\n".join((CSS_DIR / name).read_text(encoding="utf-8") for name in CSS_FILES)
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))

    used: set[str] = set()
    for js in sorted((FRONTEND / "js").glob("*.js")):
        if js.name.endswith(".test.js"):
            continue
        source = js.read_text(encoding="utf-8")
        # class="..." in template literals, skipping interpolated segments.
        for match in re.findall(r'class="([^"$`]*)"', source):
            used |= {c for c in match.split() if c}
        for match in re.findall(r"classList\.(?:add|remove|toggle)\(([^)]*)\)", source):
            used |= {c.strip("'\" ") for c in match.split(",") if "'" in c or '"' in c}
        for match in re.findall(r"className = ['\"]([^'\"]+)", source):
            used |= set(match.split())

    for match in re.findall(r'class="([^"]*)"', INDEX.read_text(encoding="utf-8")):
        used |= set(match.split())

    undefined = sorted(c for c in used - defined if c)
    assert not undefined, f"CSS classes used but never defined: {undefined}"
