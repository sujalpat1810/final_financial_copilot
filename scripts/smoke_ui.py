"""
Pre-demo smoke test: drive the real frontend in a real browser.

What this covers that nothing else does
───────────────────────────────────────
The pytest suite checks the API contract and the JS modules, but both sides are
stubbed — nothing exercises a browser actually talking to a running server. Every
failure that has mattered in practice lived in that gap: a citation that renders
but does not open, a spinner that never clears, a module that 404s so the page
loads blank and healthy-looking.

This asks the only question worth asking before a demo: if I type a question into
the box, does a cited answer appear on screen?

It is NOT part of `pytest`. It needs a running server, the models loaded, network
egress and an API key — none of which belong in a unit-test run.

Usage
─────
    # in one terminal
    uvicorn app.main:app --port 8000

    # in another
    pip install playwright && python -m playwright install chromium
    python -m scripts.smoke_ui
    python -m scripts.smoke_ui --base http://127.0.0.1:8095 --shots out/

Exit code is 0 only if every check passes, so it can gate a demo or a deploy.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# The questions below are demo-corpus specific. ANSWERABLE is phrased to land on
# the consolidated statement pages rather than the Board's-report summary table:
# the summary table carries no basis, so the obvious phrasing returns citations
# labelled "Basis unknown" — correct, but it buries the provenance story.
ANSWERABLE = "What was TCS consolidated revenue in FY2024-25?"
# Wipro is not indexed. This must be refused by the entity gate (app/entities.py),
# NOT answered from Infosys or TCS passages.
REFUSED = "What was Wipro's revenue in FY2025?"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((bool(ok), name, detail))
    print("  %s  %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    return bool(ok)


def wait_for_answer(page, before: int, timeout_ms: int) -> bool:
    """
    Wait for a NEW finished exchange.

    Waiting on a card selector alone is wrong: the previous answer's card is
    already in the DOM, so the wait returns instantly and every later assertion
    reads the wrong exchange.
    """
    try:
        page.wait_for_function(
            """n => {
              const ex = document.querySelectorAll('.exchange');
              if (ex.length <= n) return false;
              const last = ex[ex.length - 1];
              return !last.querySelector('.spinner') && !!last.querySelector('.card-bd');
            }""",
            arg=before, timeout=timeout_ms)
        return True
    except Exception:
        return False


def read_last(page) -> dict:
    return page.evaluate("""() => {
      const ex = document.querySelectorAll('.exchange');
      const last = ex[ex.length - 1];
      if (!last) return null;
      const card = last.querySelector('.card');
      const bd = last.querySelector('.card-bd');
      return {
        abstained: card ? card.classList.contains('abstained') : null,
        label: (last.querySelector('.card-hd .lbl') || {}).textContent || '',
        why: (last.querySelector('.conf-why') || {}).textContent || '',
        body: bd ? bd.innerText.replace(/\\s+/g, ' ').trim() : '',
        chips: last.querySelectorAll('.prov').length,
        openableChips: last.querySelectorAll('button.prov:not(.inert)').length,
        evidenceRows: last.querySelectorAll('button.src').length,
        nearRows: last.querySelectorAll('button.near-row').length,
        spinner: !!last.querySelector('.spinner'),
      };
    }""")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--shots", type=Path, default=None,
                    help="directory to write screenshots into")
    ap.add_argument("--timeout", type=int, default=150,
                    help="seconds to wait for one answer (default 150)")
    args = ap.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed.\n"
              "  pip install playwright && python -m playwright install chromium")
        return 2

    if args.shots:
        args.shots.mkdir(parents=True, exist_ok=True)

    def shot(page, name):
        if args.shots:
            page.screenshot(path=str(args.shots / (name + ".png")))

    print("base: %s\n" % args.base)
    tmo = args.timeout * 1000

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        console_errors: list[str] = []
        page.on("console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append("pageerror: " + str(e)))
        page.on("requestfailed",
                lambda r: console_errors.append("requestfailed: " + r.url))

        # ── The page loads ────────────────────────────────────────────────────
        print("loading the app")
        try:
            page.goto(args.base.rstrip("/") + "/app/", wait_until="networkidle",
                      timeout=60000)
        except Exception as e:
            check(False, "GET /app loads", str(e)[:70])
            browser.close()
            return 1
        page.wait_for_timeout(1500)

        boot = page.evaluate("""() => ({
          docs: document.querySelectorAll('#docList button, #docList li').length,
          seeds: document.querySelectorAll('.seed, .seeds button').length,
          unreachable: /unreachable/i.test(document.body.innerText),
          composer: !!document.getElementById('queryInput'),
          askBtn: !!document.getElementById('askBtn'),
        })""")
        check(boot["composer"] and boot["askBtn"], "composer and ask button present")
        check(not boot["unreachable"], "header does not report the service unreachable")
        check(boot["docs"] > 0, "sidebar lists indexed documents",
              "%d documents" % boot["docs"])
        shot(page, "01-boot")

        box = page.locator("#queryInput")

        # ── An answerable question produces a cited answer ────────────────────
        print("\nasking an answerable question")
        before = page.locator(".exchange").count()
        box.click(); box.fill(""); box.type(ANSWERABLE, delay=4)
        t0 = time.perf_counter()
        page.locator("#askBtn").click()

        if not wait_for_answer(page, before, tmo):
            check(False, "answer renders", "timed out after %ds" % args.timeout)
            shot(page, "02-timeout")
            browser.close()
            return 1
        elapsed = time.perf_counter() - t0
        a = read_last(page)

        check(a["abstained"] is False, "did not abstain on an answerable question")
        check(not a["spinner"], "spinner cleared")
        check(len(a["body"]) > 60, "answer body has content",
              "%d chars, %.1fs" % (len(a["body"]), elapsed))
        check(a["chips"] > 0, "answer carries citation chips", "%d" % a["chips"])
        check(a["evidenceRows"] > 0, "evidence list rendered",
              "%d sources" % a["evidenceRows"])
        shot(page, "02-answer")

        # ── An unindexed company is refused ───────────────────────────────────
        print("\nasking about a company that is not indexed")
        before = page.locator(".exchange").count()
        box.click(); box.fill(""); box.type(REFUSED, delay=4)
        t1 = time.perf_counter()
        page.locator("#askBtn").click()

        if not wait_for_answer(page, before, tmo):
            check(False, "refusal renders", "timed out")
            shot(page, "03-timeout")
            browser.close()
            return 1
        refuse_elapsed = time.perf_counter() - t1
        r = read_last(page)

        check(r["abstained"] is True, "abstained on an unindexed company")
        check("Wipro" in r["body"], "refusal names the company")
        check("Infosys" in r["body"] or "TCS" in r["body"],
              "refusal states what IS indexed")
        check(r["chips"] == 0, "no citations offered for a refused question")
        # The gate runs before generation, so a refusal must be much faster than
        # an answer. If it isn't, generation was called and the gate is bypassed.
        check(refuse_elapsed < elapsed,
              "refusal skipped generation",
              "%.1fs vs %.1fs" % (refuse_elapsed, elapsed))
        shot(page, "03-refusal")

        # ── A citation opens its source page ──────────────────────────────────
        print("\nopening a citation")
        chip = page.locator("button.prov:not(.inert)").first
        if chip.count():
            chip.click()
            page.wait_for_timeout(7000)
            panel = page.evaluate("""() => {
              const p = document.getElementById('panel');
              return {
                open: p ? !p.hasAttribute('hidden') &&
                          getComputedStyle(p).display !== 'none' : false,
                doc: (document.getElementById('panelDoc') || {}).textContent || '',
                page: (document.getElementById('panelPage') || {}).textContent || '',
                canvas: !!document.querySelector('#panel canvas'),
              };
            }""")
            check(panel["open"], "source panel opens")
            check(panel["canvas"], "PDF page renders",
                  "%s %s" % (panel["doc"].strip(), panel["page"].strip()))
            shot(page, "04-source")
        else:
            check(False, "an openable citation exists")

        # ── Nothing broke in the console ──────────────────────────────────────
        print("\nbrowser console")
        # PDF.js reports benign range-request warnings on some builds; only real
        # errors and failed requests are collected above.
        check(not console_errors, "no console errors or failed requests",
              "; ".join(console_errors[:2])[:70] if console_errors else "")

        browser.close()

    failed = [r for r in results if not r[0]]
    print("\n%d checks, %d failed" % (len(results), len(failed)))
    if failed:
        print("\nFAILED:")
        for _, name, detail in failed:
            print("  - %s %s" % (name, detail))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
