"""
Answer generation, with an extractive fallback when no model is reachable.

Why google-genai (not google-generativeai)?
  google-generativeai is the legacy SDK (deprecated as of early 2025).
  google-genai is the current, actively maintained official SDK and supports
  the full Gemini 2.5 family including streaming + thinking models.

Fallback behaviour:
  If no API key is configured or the call fails, the top reranked chunks are
  returned verbatim so the pipeline still produces useful output without a key.
  answer_source distinguishes the two as "generated" vs "extractive" — vendor
  neutral, because what matters to the reader is whether the answer was
  synthesised or quoted, not which model produced it.

Prompt design:
  The prompt exists to stop one specific failure: a figure stated without saying
  which entity, fiscal year and basis it belongs to.  For the accountants and
  lawyers using this, an unqualified number is worse than no number.

  Each source block is labelled Entity | Fiscal year | Basis | Page, and the
  rules distinguish two things that are easy to conflate:

    * the label's fiscal year is the year of the REPORT the excerpt came from;
    * the column header inside the excerpt is the year a given NUMBER belongs to.

  Financial statements print the current year beside comparative columns, so one
  table routinely holds figures for two years. The prompt requires comparative
  figures to be named as such.

  An undetermined basis is passed through as "Basis not determined" rather than
  omitted — a named unknown is something the model can report, where a missing
  field reads as an oversight it may quietly paper over.

  Temperature is left at the SDK default; financial Q&A benefits from the
  model's standard calibration (low creativity, high accuracy).

  This module does NOT decide whether to answer at all. The abstention gate is
  app/confidence.assess and is applied by the caller before generation.
"""

from __future__ import annotations

import logging
import os
import textwrap
import time
from typing import Any

from app.config import cfg
from app.models import RetrievedChunk

log = logging.getLogger(__name__)


# ── Prompt registry ───────────────────────────────────────────────────────────
# One prompt per task, all built on a shared discipline core.  The core exists
# to stop one failure: a figure or legal claim stated without saying where it
# came from.  Task prompts add only what their task needs — the core is not
# repeated in prose there, it is prepended verbatim, so every task answers from
# byte-identical ground rules.

_CORE_RULES = textwrap.dedent("""
    You are an assistant used by chartered accountants.  They rely on your
    answers professionally, so an unqualified figure or an uncited legal claim
    is worse than none at all.

    You are given numbered excerpts.  Each is labelled with its source, the
    type of document it came from, and its page number.

    CITATIONS
    Cite the page number for every factual claim, like "[Page 3]".  Never cite
    a page that is not in the provided excerpts, and never invent a source.

    SOURCE CLASSES
    Each source label names the document type.  Tag statements according to
    where they come from: [Statute] for provisions of law, [Client document]
    for facts from the client's own documents (invoices, notices, financials),
    and [General] for anything not grounded in the excerpts.  Keep [General]
    statements to an absolute minimum.

    OTHER RULES
    - Use only the provided excerpts.  Do not draw on outside knowledge.
    - If the answer is not present in the excerpts, say exactly:
      "The answer was not found in the provided context."
    - Do not invent or extrapolate numbers beyond what is stated.
    - Report figures in the units the source uses and say which.
    - Be concise but complete.  Bullet points and small tables are fine.
""").strip()

_ANNUAL_REPORT_RULES = textwrap.dedent("""
    QUALIFYING FIGURES — the most important rule
    Every figure you state must carry its entity, fiscal year and basis.
    "Revenue was Rs 1,62,990 crore" is not an acceptable answer.
    "Infosys consolidated revenue for FY2024-25 was Rs 1,62,990 crore [Page 276]" is.

    THE REPORT'S YEAR IS NOT THE FIGURE'S YEAR
    The label tells you which report an excerpt came from.  The column header
    INSIDE the excerpt tells you which year a particular number belongs to.
    Financial statements print the current year beside comparative columns, so
    a single table routinely contains figures for two different years.  When a
    figure comes from a comparative column, say so explicitly.  If you cannot
    tell which column a number belongs to, say that rather than guessing.

    BASIS
    Standalone and consolidated figures differ materially and are not
    interchangeable.  Use the basis given in the label.  Where the label says
    "Basis not determined", state that.  Never infer it.

    UNQUALIFIED QUESTIONS
    If the question does not specify an entity, fiscal year or basis, and the
    excerpts support more than one answer, give ALL of them, each clearly
    labelled.  Never silently pick one.  A short table is the clearest format.
""").strip()

PROMPTS: dict[str, str] = {
    # The pre-CA default: annual-report Q&A discipline, unchanged in substance.
    "default": _CORE_RULES + "\n\n" + _ANNUAL_REPORT_RULES,

    "qa_statute": _CORE_RULES + "\n\n" + textwrap.dedent("""
        STATUTE QUESTIONS
        Quote provision language precisely where it matters, and name the
        section and sub-section for every provision you rely on, like
        "section 16(2)(aa) of the CGST Act [Page 1]".  If more than one
        provision applies, list each one.  Where a provision has conditions,
        enumerate them rather than summarising them away.
    """).strip(),

    "qa_client_docs": _CORE_RULES + "\n\n" + textwrap.dedent("""
        CLIENT DOCUMENT QUESTIONS
        Every figure must carry the client name, the period or fiscal year it
        belongs to, and its page, like "[Page 2]".  Where the excerpt shows a
        comparative (prior-year) column, name which year each figure belongs
        to.  Where the label says "Basis not determined", state that rather
        than inferring a basis.
    """).strip() + "\n\n" + _ANNUAL_REPORT_RULES,

    "recon_explain": _CORE_RULES + "\n\n" + textwrap.dedent("""
        RECONCILIATION EXCEPTION
        You are given ONE reconciliation exception between a client's purchase
        register (books) and GSTR-2B, as structured data.  All arithmetic has
        already been done deterministically — do not recompute or dispute the
        numbers.  Your job is the explanation a CA would give a colleague:

        - State the likely cause in at most three sentences, using the
          vocabulary of GST practice (e.g. "the supplier has not filed GSTR-1
          for the period, so the credit is not yet available in GSTR-2B and
          fails the condition in section 16(2)(aa)").
        - Then ONE recommended action line (e.g. "follow up with the supplier
          to report the invoice; do not avail the credit until it appears").

        Respond ONLY with a JSON object of the form
        {"cause": "...", "recommended_action": "..."}
        with no surrounding prose or code fences.
    """).strip(),

    "notice_extract": _CORE_RULES + "\n\n" + textwrap.dedent("""
        EXTRACTING A NOTICE'S PARTICULARS
        You are given the text of one tax notice.  Extract its particulars
        into JSON — read them off the notice, never infer or invent one.
        A particular the notice does not state is null.

        Respond ONLY with a JSON object of this exact shape, no surrounding
        prose or code fences:
        {"notice_type": "...", "reference_no": "...", "gstin": "...",
         "period": "...", "alleged_discrepancy": "one-paragraph summary",
         "amount": 12345.67, "sections_cited": ["..."], "reply_form": "...",
         "reply_due_days": 30}
    """).strip(),

    "notice_reply": _CORE_RULES + "\n\n" + textwrap.dedent("""
        DRAFTING A REPLY TO A SCRUTINY NOTICE
        Draft a formal reply in FORM GST ASMT-11 register — Indian professional
        legal correspondence, addressed to the proper officer.  Use EXACTLY
        this skeleton, filling each section from the provided excerpts and the
        discrepancy details:

        ## Legal Header
        (reference number, GSTIN, tax period, the notice being replied to)

        ## Statement of Facts
        (what the notice alleges; what the taxpayer's records show)

        ## Point-wise Submissions
        (numbered rebuttals; EVERY point must cite the governing provision
        from the excerpts with its page, like "section 61(1) [Page 1]")

        ## Prayer
        (the relief sought — dropping of proceedings under FORM GST ASMT-12)

        ## Request for Personal Hearing
        (expressly invoke the right to be heard under section 75(4))

        This is a DRAFT for review and signature by a chartered accountant.
        Do not fabricate facts not present in the inputs; where a fact is
        needed but unavailable, leave a bracketed placeholder like
        [ATTACH: supplier ledger extract].
    """).strip(),

    "dual_act": _CORE_RULES + "\n\n" + textwrap.dedent("""
        ACT-VERSION DISCIPLINE
        The excerpts you are given come from ONE version of the Income-tax Act
        (either the 1961 Act or the 2025 Act).  Answer strictly from that
        version.  State the Act name and section number prominently at the
        start of your answer.  If the excerpt contains a mapping note to the
        other Act's numbering, report the mapped section number and repeat the
        note's caution to verify against the enacted text.  Never mix section
        numbers from the two Acts without labelling which Act each belongs to.
    """).strip(),
}


def _source_label(r: RetrievedChunk, index: int) -> str:
    """
    One header line carrying everything needed to qualify a figure.

    "Basis not determined" is stated explicitly rather than omitted: a missing
    field reads as an oversight the model may paper over, whereas a named
    unknown is something it can report.
    """
    m = r.chunk.metadata
    # Document type maps to the source class the model tags statements with —
    # derived deterministically from metadata, never asserted by the model.
    doc_type_label = {
        "statute": "Statute",
        "invoice": "Client document (invoice)",
        "notice": "Client document (tax notice)",
        "financials": "Client document (financial statements)",
        "register": "Client document (register)",
    }.get(m.doc_type or "", "Document type not recorded")
    parts = [
        m.client or m.entity or "Entity not recorded",
        doc_type_label,
        m.fiscal_year or "Fiscal year not recorded",
        f"{m.basis.capitalize()} financial statements" if m.basis else "Basis not determined",
        f"Page {m.page_number}",
    ]
    header = f"[Source {index}] " + " | ".join(parts)
    if m.section_title:
        header += f"\n    Section: {m.section_title}"
    return header


def _build_prompt(question: str, chunks: list[RetrievedChunk],
                  task: str = "default") -> str:
    context_blocks = [
        f"{_source_label(r, i)}\n{r.chunk.text}"
        for i, r in enumerate(chunks, start=1)
    ]

    context_text = "\n\n---\n\n".join(context_blocks)
    system_prompt = PROMPTS.get(task, PROMPTS["default"])

    return (
        f"{system_prompt}\n\n"
        f"=== CONTEXT EXCERPTS ===\n\n{context_text}\n\n"
        f"=== QUESTION ===\n\n{question}\n\n"
        f"=== ANSWER ==="
    )


# ── Extractive fallback ───────────────────────────────────────────────────────

def _extractive_answer(question: str, chunks: list[RetrievedChunk]) -> str:
    """
    Return the top chunks verbatim when no model is available.

    Deliberately says nothing about which vendor or key is missing: this string is
    shown to the user, and confidentiality-conscious firms should not learn the
    generation backend from an error message.  The operator sees the real reason in
    the server log.  answer_source="extractive" is what the UI renders.

    Provenance is carried here too — an extractive answer is still a set of figures
    that must not be read without knowing their entity, year and basis.
    """
    lines = [
        "No generated answer is available, so the most relevant retrieved passages "
        "are shown verbatim below.\n",
    ]
    for i, r in enumerate(chunks, start=1):
        lines.append(
            f"\n{_source_label(r, i)}\n{r.chunk.text[:600]}"
            + ("..." if len(r.chunk.text) > 600 else "")
        )
    return "\n".join(lines)


# ── Providers ─────────────────────────────────────────────────────────────────
# Two backends, one prompt, one retry policy, one fallback.  Only the four
# functions below know which vendor is in use; everything downstream — the
# abstention gate, answer_source, the response schema — stays vendor-neutral.
#
# Each _call_* returns the whole answer; each _stream_* yields fragments.  Both
# raise on failure, so the shared retry and extractive fallback handle every
# provider identically.


class ProviderUnavailable(RuntimeError):
    """The provider's SDK is not installed. Distinct from a call that failed."""


def _gemini_client():
    try:
        from google import genai
    except ImportError:
        raise ProviderUnavailable(
            "google-genai is not installed. Run: pip install -r requirements.txt"
        )
    return genai.Client(api_key=cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY"))


def _groq_client():
    try:
        from groq import Groq
    except ImportError:
        raise ProviderUnavailable(
            "groq is not installed. Run: pip install -r requirements.txt"
        )
    return Groq(api_key=cfg.groq_api_key or os.environ.get("GROQ_API_KEY"))


def _call_gemini(prompt: str) -> str:
    response = _gemini_client().models.generate_content(
        model=cfg.gemini_model, contents=prompt,
    )
    return response.text or ""


def _stream_gemini(prompt: str):
    for piece in _gemini_client().models.generate_content_stream(
        model=cfg.gemini_model, contents=prompt,
    ):
        text = getattr(piece, "text", None)
        if text:
            yield text


def _groq_messages(prompt: str) -> list[dict]:
    # The prompt already carries its own instructions and the labelled sources,
    # so it goes in whole as a single user turn rather than being split into a
    # system message — keeping one prompt string means both providers are
    # answering from byte-identical instructions.
    return [{"role": "user", "content": prompt}]


def _call_groq(prompt: str) -> str:
    completion = _groq_client().chat.completions.create(
        model=cfg.groq_model, messages=_groq_messages(prompt),
    )
    return completion.choices[0].message.content or ""


def _stream_groq(prompt: str):
    stream = _groq_client().chat.completions.create(
        model=cfg.groq_model, messages=_groq_messages(prompt), stream=True,
    )
    for chunk in stream:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        text = getattr(delta, "content", None) if delta else None
        if text:
            yield text


def _anthropic_client():
    try:
        import anthropic
    except ImportError:
        raise ProviderUnavailable(
            "anthropic is not installed. Run: pip install -r requirements.txt"
        )
    return anthropic.Anthropic(
        api_key=cfg.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    )


# Answers here run a few hundred to ~1,500 tokens, but on current Claude models
# max_tokens caps thinking + response text together and thinking is on by
# default, so the ceiling needs real headroom beyond the visible answer.
_ANTHROPIC_MAX_TOKENS = 16000


def _call_anthropic(prompt: str) -> str:
    # Same single-user-turn shape as Groq: the prompt carries its own
    # instructions and labelled sources, so all providers answer from
    # byte-identical input.
    response = _anthropic_client().messages.create(
        model=cfg.anthropic_model,
        max_tokens=_ANTHROPIC_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    # Safety classifiers can decline with HTTP 200 + stop_reason "refusal" and
    # empty content — raising here routes it through the shared fallback so the
    # reader gets an extractive answer instead of an empty one.
    if response.stop_reason == "refusal":
        raise RuntimeError("provider declined the request (refusal)")
    return "".join(
        block.text for block in response.content if block.type == "text"
    )


def _stream_anthropic(prompt: str):
    with _anthropic_client().messages.stream(
        model=cfg.anthropic_model,
        max_tokens=_ANTHROPIC_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            yield text
        # A refusal mid-stream would end the stream early; the caller already
        # treats a zero-character stream as a failure, and a partial stream is
        # kept and labelled — both existing paths handle it.


_CALL = {"gemini": _call_gemini, "groq": _call_groq, "claude": _call_anthropic}
_STREAM = {"gemini": _stream_gemini, "groq": _stream_groq, "claude": _stream_anthropic}


# ── Transient-failure retry ───────────────────────────────────────────────────
# Observed on the demo corpus: a 503 "model is currently experiencing high
# demand" downgraded a perfectly answerable question to an extractive answer.
# The fallback is correct behaviour, but spending it on a blip that clears in a
# second is a waste — and the reader cannot tell that degraded answer apart from
# one where the evidence was genuinely thin.
#
# Deliberately small: three attempts at 1s and 2s adds at most 3s to the worst
# case, against a generation call that already runs 2-20s.  Retrying a
# non-transient error (bad key, retired model) would just add latency to a
# failure that is never going to succeed, so those break out immediately.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = 1.0

# Matched on the status code in the exception text rather than on SDK exception
# classes: google-genai raises ClientError/ServerError with the code embedded,
# and pinning to those class names would break silently on an SDK refactor.
_TRANSIENT_MARKERS = (
    "429",              # rate limited
    "500", "502", "503", "504",
    "resource_exhausted",
    "unavailable",
    "deadline_exceeded",
    "internal error",
)


# A 429 is usually a per-minute burst and worth retrying. A 429 that names a
# PER-DAY quota is not: the window is tomorrow, no backoff reaches it, and each
# retry spends another request from an allowance that is already gone. Observed
# on a free-tier key capped at 20 requests per day — three attempts turned one
# exhausted answer into three.
_EXHAUSTED_MARKERS = (
    "perday",                    # quotaId: GenerateRequestsPerDayPerProjectPerModel
    "requests per day",
    "free_tier_requests",
    "quota exceeded for metric",
)


def _is_daily_quota(error: Exception) -> bool:
    """A 429 whose window is a day rather than a minute."""
    text = str(error).lower().replace(" ", "")
    return "429" in text and any(
        m.replace(" ", "") in text for m in _EXHAUSTED_MARKERS
    )


def _is_transient(error: Exception) -> bool:
    """
    Whether retrying could plausibly succeed.

    A 404 (retired model) or 401/403 (bad key) is a configuration fault: it will
    fail identically three times and the only effect of retrying is to make the
    request slower before it degrades.  A daily quota is the same in practice —
    and worse, retrying spends more of a budget that has already run out.
    """
    if _is_daily_quota(error):
        log.error(
            "Generation quota for the day is exhausted — not retrying. Every "
            "answer will be extractive until it resets. Check the API plan."
        )
        return False
    text = str(error).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


# ── Capability check ──────────────────────────────────────────────────────────

def generation_available() -> bool:
    """
    Whether a generated (not extractive) answer is actually reachable.

    A configured key is necessary but not sufficient: generate_answer imports the
    SDK lazily inside its try/except, so a missing google-genai install degrades
    every answer to extractive while /health — if it only checked the key — kept
    reporting generation as available.  That combination is the worst one to
    debug, because the symptom is quiet and the status endpoint denies it.

    This deliberately does NOT make a network call.  /health is polled, and a
    live probe per poll would burn quota and turn a status check into a
    dependency on the model being up.  A retired model id therefore still reads
    as available here; the generate call logs that one.
    """
    provider = cfg.generation_provider
    if provider not in _CALL or not cfg.generation_api_key:
        return False
    try:
        if provider == "gemini":
            from google import genai  # noqa: F401
        elif provider == "claude":
            import anthropic  # noqa: F401
        else:
            from groq import Groq  # noqa: F401
    except ImportError:
        log.warning(
            "%s is selected and its key is set, but its SDK is not installed; "
            "answers will fall back to extractive. Run: pip install -r "
            "requirements.txt", provider,
        )
        return False
    return True


# ── Streaming ─────────────────────────────────────────────────────────────────

def stream_answer(question: str, chunks: list[RetrievedChunk], task: str = "default"):
    """
    Yield (kind, payload) as the answer is written.

    kind is "delta" for a fragment of text, or "done" for the terminal event
    carrying (full_text, answer_source).  Exactly one "done" is yielded, always,
    including on every failure path — the caller can therefore treat "done" as
    the single place a response is finalised rather than duplicating fallback
    handling.

    This is a SYNCHRONOUS generator. The SDK's streaming call is blocking, so the
    caller must drive it off the event loop (see main.py, iterate_in_threadpool).

    Retry policy differs from generate_answer deliberately. A transient failure
    is only retried while nothing has been emitted yet: once fragments have
    reached the reader, restarting would replay text they have already seen, and
    silently discarding the partial answer to fall back to an extractive one
    would be worse still.  After first output, a failure ends the stream with
    what was actually produced.
    """
    if not chunks:
        yield "done", ("No relevant context was found in the indexed documents.",
                       "extractive")
        return

    provider = cfg.generation_provider
    if provider not in _STREAM or not cfg.generation_api_key:
        log.warning("No generation provider configured; returning extractive answer.")
        yield "done", (_extractive_answer(question, chunks), "extractive")
        return

    prompt = _build_prompt(question, chunks, task)
    last_error: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        parts: list[str] = []
        try:
            for text in _STREAM[provider](prompt):
                parts.append(text)
                yield "delta", text

            if parts:
                yield "done", ("".join(parts), "generated")
                return
            # A stream that completed without a single character is a failure
            # wearing a success's clothes; treat it as one.
            last_error = RuntimeError("stream produced no text")

        except ProviderUnavailable as e:
            log.warning("%s; falling back to extractive.", e)
            yield "done", (_extractive_answer(question, chunks), "extractive")
            return

        except Exception as e:  # noqa: BLE001 — must degrade, never raise
            last_error = e
            if parts:
                # Already on screen. Keep it, and say so rather than pretending.
                log.warning("Stream failed after %d fragments (%s); "
                            "returning the partial answer.", len(parts), e)
                yield "done", ("".join(parts), "generated")
                return

        if attempt + 1 < _MAX_ATTEMPTS and _is_transient(last_error):
            delay = _BACKOFF_SECONDS * (2 ** attempt)
            log.warning("Stream attempt %d/%d failed transiently (%s); "
                        "retrying in %.1fs.",
                        attempt + 1, _MAX_ATTEMPTS, last_error, delay)
            time.sleep(delay)
            continue
        break

    log.warning("Streaming failed after %d attempt(s) (%s); falling back to "
                "extractive answer.", _MAX_ATTEMPTS, last_error)
    yield "done", (_extractive_answer(question, chunks), "extractive")


# ── Main generate function ────────────────────────────────────────────────────

def generate_answer(question: str, chunks: list[RetrievedChunk],
                    task: str = "default") -> tuple[str, str]:
    """
    Returns (answer_text, answer_source) where answer_source is
    "generated" or "extractive".

    These values are deliberately vendor-neutral.  What matters to the reader is
    whether the answer was synthesised or quoted verbatim, not which model produced
    it — and naming a third-party model in the response invites the "where do our
    documents go?" question before there is a good answer to it.  The model name
    stays in config for debugging.

    Callers must check the abstention gate first (see app/confidence.assess).  This
    function assumes the evidence has already cleared the floor; it does not
    re-check, so calling it on thin evidence would generate an answer that should
    never have existed.
    """
    if not chunks:
        return "No relevant context was found in the indexed documents.", "extractive"

    provider = cfg.generation_provider
    if provider not in _CALL or not cfg.generation_api_key:
        log.warning("No generation provider configured; returning extractive answer.")
        return _extractive_answer(question, chunks), "extractive"

    prompt = _build_prompt(question, chunks, task)

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return _CALL[provider](prompt), "generated"

        except ProviderUnavailable as e:
            log.warning("%s; falling back to extractive answer.", e)
            return _extractive_answer(question, chunks), "extractive"

        except Exception as e:  # noqa: BLE001 — any failure must degrade, not raise
            last_error = e
            if attempt + 1 < _MAX_ATTEMPTS and _is_transient(e):
                delay = _BACKOFF_SECONDS * (2 ** attempt)
                log.warning(
                    "Generation attempt %d/%d failed transiently (%s); retrying in %.1fs.",
                    attempt + 1, _MAX_ATTEMPTS, e, delay,
                )
                time.sleep(delay)
                continue
            break

    # The exception can carry the model name and account details, so it goes to
    # the log, not into the answer the user reads.
    log.warning(
        "Generation failed after %d attempt(s) (%s); falling back to extractive answer.",
        _MAX_ATTEMPTS, last_error,
    )
    return _extractive_answer(question, chunks), "extractive"
