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
from typing import Any

from app.config import cfg
from app.models import RetrievedChunk

log = logging.getLogger(__name__)


# ── Prompt template ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT = textwrap.dedent("""
    You are a financial research assistant used by chartered accountants and
    lawyers.  They rely on your answers professionally, so an unqualified figure is
    worse than no figure at all.

    You are given numbered excerpts from annual reports.  Each is labelled with the
    entity, the fiscal year of the REPORT it was published in, the basis of the
    financial statements it sits in, and its page number.

    Cite the page number for every factual claim, like "[Page 276]".

    QUALIFYING FIGURES — the most important rule
    Every figure you state must carry its entity, fiscal year and basis.
    "Revenue was Rs 1,62,990 crore" is not an acceptable answer.
    "Infosys consolidated revenue for FY2024-25 was Rs 1,62,990 crore [Page 276]" is.

    THE REPORT'S YEAR IS NOT THE FIGURE'S YEAR
    The label tells you which report an excerpt came from.  The column header
    INSIDE the excerpt tells you which year a particular number belongs to.  These
    are different facts and you must not conflate them.  Financial statements print
    the current year beside one or more comparative columns, so a single table
    routinely contains figures for two different years.

    When a figure comes from a comparative column, say so explicitly:
      "The FY2023-24 comparative column of the same statement shows
       Rs 1,53,670 crore [Page 276]."
    If you cannot tell which column a number belongs to, say that rather than
    guessing.

    BASIS
    Standalone and consolidated figures differ materially and are not
    interchangeable.  Use the basis given in the label.  Where the label says
    "Basis not determined", state that the basis could not be determined for that
    figure.  Never infer it, and never assume consolidated because it is more
    commonly quoted.

    UNQUALIFIED QUESTIONS
    If the question does not specify an entity, fiscal year or basis, and the
    excerpts support more than one answer, give ALL of them, each clearly labelled.
    Never silently pick one.  A short table is the clearest format for this.

    OTHER RULES
    - Use only the provided excerpts.  Do not draw on outside knowledge.
    - If the answer is not present in the excerpts, say exactly:
      "The answer was not found in the provided context."
    - Do not invent or extrapolate numbers beyond what is stated.
    - Report figures in the units the source uses (Rs crore, Rs lakh) and say which.
    - Be concise but complete.  Bullet points and small tables are fine.
""").strip()


def _source_label(r: RetrievedChunk, index: int) -> str:
    """
    One header line carrying everything needed to qualify a figure.

    "Basis not determined" is stated explicitly rather than omitted: a missing
    field reads as an oversight the model may paper over, whereas a named
    unknown is something it can report.
    """
    m = r.chunk.metadata
    parts = [
        m.entity or "Entity not recorded",
        m.fiscal_year or "Fiscal year not recorded",
        f"{m.basis.capitalize()} financial statements" if m.basis else "Basis not determined",
        f"Page {m.page_number}",
    ]
    header = f"[Source {index}] " + " | ".join(parts)
    if m.section_title:
        header += f"\n    Section: {m.section_title}"
    return header


def _build_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    context_blocks = [
        f"{_source_label(r, i)}\n{r.chunk.text}"
        for i, r in enumerate(chunks, start=1)
    ]

    context_text = "\n\n---\n\n".join(context_blocks)

    return (
        f"{_SYSTEM_PROMPT}\n\n"
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


# ── Main generate function ────────────────────────────────────────────────────

def generate_answer(question: str, chunks: list[RetrievedChunk]) -> tuple[str, str]:
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

    api_key = cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY")

    if not api_key:
        log.warning("No generation API key configured; returning extractive answer.")
        return _extractive_answer(question, chunks), "extractive"

    prompt = _build_prompt(question, chunks)

    try:
        from google import genai  # google-genai SDK (NOT google-generativeai)

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=cfg.gemini_model,
            contents=prompt,
        )
        return response.text, "generated"

    except Exception as e:
        # The exception can carry the model name and account details, so it goes to
        # the log, not into the answer the user reads.
        log.warning("Generation call failed (%s); falling back to extractive answer.", e)
        return _extractive_answer(question, chunks), "extractive"
