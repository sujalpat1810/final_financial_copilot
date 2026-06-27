"""
Answer generation: Gemini 2.5 Flash via google-genai SDK, with extractive fallback.

Why google-genai (not google-generativeai)?
  google-generativeai is the legacy SDK (deprecated as of early 2025).
  google-genai is the current, actively maintained official SDK and supports
  the full Gemini 2.5 family including streaming + thinking models.

Fallback behaviour:
  If GEMINI_API_KEY is absent or the API call fails, we return the top
  reranked chunks directly with a clear label so the pipeline still produces
  useful output for testing without any API key.

Prompt design:
  - Numbered source blocks let the LLM cite page numbers inline.
  - Explicit instruction to say "not found in the provided context" prevents
    the model from hallucinating facts not present in the retrieved chunks.
  - Temperature is left at the SDK default; financial Q&A benefits from the
    model's standard calibration (low creativity, high accuracy).
"""

from __future__ import annotations

import os
import textwrap
from typing import Any

from app.config import cfg
from app.models import RetrievedChunk


# ── Prompt template ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT = textwrap.dedent("""
    You are a financial research analyst assistant.  You are given numbered excerpts
    from annual reports and financial filings, each labelled with its source document
    and page number.  Your job is to answer the user's question accurately, citing
    the page number for every factual claim you make (e.g. "[Page 12]").

    Rules:
    - Only use information from the provided context excerpts.
    - If the answer is not present in the excerpts, say exactly:
      "The answer was not found in the provided context."
    - Do not invent or extrapolate numbers beyond what is stated.
    - Be concise but complete.  Bullet points are fine for multi-part answers.
""").strip()


def _build_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    context_blocks = []
    for i, r in enumerate(chunks, start=1):
        m = r.chunk.metadata
        header = f"[Source {i}] {m.doc_name}, Page {m.page_number}"
        if m.section_title:
            header += f" — {m.section_title}"
        if m.fiscal_year:
            header += f" ({m.fiscal_year})"
        context_blocks.append(f"{header}\n{r.chunk.text}")

    context_text = "\n\n---\n\n".join(context_blocks)

    return (
        f"{_SYSTEM_PROMPT}\n\n"
        f"=== CONTEXT EXCERPTS ===\n\n{context_text}\n\n"
        f"=== QUESTION ===\n\n{question}\n\n"
        f"=== ANSWER ==="
    )


# ── Extractive fallback ───────────────────────────────────────────────────────

def _extractive_answer(question: str, chunks: list[RetrievedChunk]) -> str:
    """Return the top chunks verbatim when no LLM is available."""
    lines = [
        "⚠️  No GEMINI_API_KEY found — returning extractive answer (top retrieved chunks).\n",
        f"Question: {question}\n",
    ]
    for i, r in enumerate(chunks, start=1):
        m = r.chunk.metadata
        lines.append(
            f"\n[Chunk {i}] {m.doc_name}, Page {m.page_number}"
            + (f" — {m.section_title}" if m.section_title else "")
            + f"\n{r.chunk.text[:600]}"
            + ("..." if len(r.chunk.text) > 600 else "")
        )
    return "\n".join(lines)


# ── Main generate function ────────────────────────────────────────────────────

def generate_answer(question: str, chunks: list[RetrievedChunk]) -> tuple[str, str]:
    """
    Returns (answer_text, answer_source) where answer_source is
    "gemini" or "extractive_fallback".
    """
    if not chunks:
        return "No relevant context was found in the indexed documents.", "extractive_fallback"

    api_key = cfg.gemini_api_key or os.environ.get("GEMINI_API_KEY")

    if not api_key:
        return _extractive_answer(question, chunks), "extractive_fallback"

    prompt = _build_prompt(question, chunks)

    try:
        from google import genai  # google-genai SDK (NOT google-generativeai)

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=cfg.gemini_model,   # "gemini-2.5-flash"
            contents=prompt,
        )
        answer = response.text
        return answer, "gemini"

    except Exception as e:
        print(f"[generation] Gemini call failed ({e}); falling back to extractive answer.")
        fallback = (
            f"⚠️  Gemini API error: {e}\n\n"
            + _extractive_answer(question, chunks)
        )
        return fallback, "extractive_fallback"
