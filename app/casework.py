"""
Notice casework: read a scrutiny notice, retrieve the governing provisions,
draft the reply — and stop at the approval gate.

Positioning matters here.  Published benchmarks (VLAIR 2025) found AI beating
professional baselines on extraction and grounded Q&A but losing on drafting —
so the draft this module produces is presented, everywhere, as A DRAFT FOR CA
REVIEW.  Nothing leaves without the approval gate in structured.decide_reply,
and "send" does not exist as an action at all in the demo.

Two separate LLM calls, each simple, each independently recoverable:
1. extract_discrepancy — one constrained call over the notice's own text,
   with a regex fallback that reads the amount/period straight off the notice
   when no provider is reachable.
2. draft_reply — retrieval pinned to statute chunks by metadata (the model
   cannot cite anything that is not a provision), then the fixed-skeleton
   notice_reply prompt.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import cfg
from app.generation import generate_answer
from app.ingestion import get_all_chunks, get_document
from app.models import Chunk, ChunkMetadata, RetrievedChunk

log = logging.getLogger(__name__)


# ── Notice text ───────────────────────────────────────────────────────────────

def notice_text(doc_id: str) -> str | None:
    """The notice's full text from the chunk store, in page/chunk order."""
    if not get_document(doc_id):
        return None
    chunks = [c for c in get_all_chunks() if c.metadata.doc_id == doc_id]
    chunks.sort(key=lambda c: (c.metadata.page_number, c.metadata.chunk_index))
    return "\n\n".join(c.text for c in chunks) or None


# ── Step 1: extract the discrepancy ───────────────────────────────────────────

def _regex_extract(text: str) -> dict[str, Any]:
    """
    Deterministic fallback: read the load-bearing particulars straight off the
    notice.  Field coverage is narrower than the model's, but every value here
    is literally present in the text.
    """
    amount = None
    m = re.search(r"Rs\.?\s*([\d,]+(?:\.\d+)?)", text)
    if m:
        amount = float(m.group(1).replace(",", ""))
    period = None
    m = re.search(r"[Tt]ax period[:\s]+([A-Za-z]+ \d{4}(?:\s*\(FY [\d\-\s]+\))?)", text)
    if m:
        period = m.group(1).strip()
    ref = None
    m = re.search(r"Reference no\.?[:\s]+(\S+)", text)
    if m:
        ref = m.group(1).strip()
    gstin = None
    m = re.search(r"GSTIN[:\s]+([0-9A-Z]{15})", text)
    if m:
        gstin = m.group(1)
    sections = sorted(set(re.findall(r"section\s+(\d+[A-Z]?)", text, re.I)))
    form = "ASMT-11" if "ASMT-11" in text else None
    return {
        "notice_type": "ASMT-10" if "ASMT-10" in text else "notice",
        "reference_no": ref,
        "gstin": gstin,
        "period": period,
        "alleged_discrepancy": None,
        "amount": amount,
        "sections_cited": [f"section {s}" for s in sections],
        "reply_form": form,
        "reply_due_days": 30 if "thirty days" in text.lower() else None,
        "extraction": "regex",
    }


def extract_discrepancy(doc_id: str) -> dict[str, Any] | None:
    """One constrained LLM call over the notice text; regex fallback."""
    text = notice_text(doc_id)
    if not text:
        return None

    fallback = _regex_extract(text)
    pseudo = RetrievedChunk(chunk=Chunk(
        chunk_id=f"{doc_id}_notice",
        text=text,
        metadata=ChunkMetadata(
            chunk_id=f"{doc_id}_notice", doc_id=doc_id, doc_name="Notice",
            page_number=1, doc_type="notice",
        ),
    ))
    try:
        answer, source = generate_answer(
            "Extract this notice's particulars.", [pseudo],
            task="notice_extract")
        if source != "generated":
            return fallback
        parsed = json.loads(answer.strip().strip("`").removeprefix("json"))
        if not isinstance(parsed, dict) or not parsed.get("alleged_discrepancy"):
            return fallback
        # The AMOUNT is the one field a wrong value on which poisons the whole
        # reply. The regex read it literally off the notice; if the model's
        # number disagrees with the literal one, the literal one wins.
        if fallback.get("amount") is not None:
            model_amount = parsed.get("amount")
            if model_amount is None or abs(float(model_amount) - fallback["amount"]) > 0.01:
                parsed["amount"] = fallback["amount"]
        parsed["extraction"] = "generated"
        return parsed
    except Exception as e:  # noqa: BLE001 — extraction must degrade, never raise
        log.warning("notice extraction failed (%s); using regex particulars", e)
        return fallback


# ── Step 2: retrieve the governing provisions ─────────────────────────────────

def retrieve_provisions(retriever, discrepancy: dict[str, Any],
                        top_n: int = 6) -> list[RetrievedChunk]:
    """
    Retrieval PINNED to statute chunks by metadata — the reply can only cite
    provisions, structurally.  The query is composed from the discrepancy's own
    vocabulary rather than fixed, so a differently-worded notice still reaches
    the right sections.
    """
    parts = ["scrutiny of returns", "input tax credit conditions",
             "explanation to proper officer", "demand proceedings"]
    alleged = discrepancy.get("alleged_discrepancy")
    if alleged:
        parts.insert(0, str(alleged)[:300])
    for section in discrepancy.get("sections_cited") or []:
        parts.append(str(section))
    query = ". ".join(parts)
    return retriever.retrieve(query=query, top_n=top_n,
                              filters={"doc_type": "statute"})


# ── Step 3: draft the reply ───────────────────────────────────────────────────

def draft_reply(discrepancy: dict[str, Any],
                provisions: list[RetrievedChunk]) -> tuple[str, str]:
    """
    Returns (draft_markdown, answer_source).  answer_source "extractive" means
    no provider was reachable — the caller should treat that as no-draft and
    fall back to showing the discrepancy + provisions without prose.
    """
    question = (
        "Draft the reply to this notice.\n\n=== NOTICE PARTICULARS ===\n"
        + json.dumps({k: v for k, v in discrepancy.items()
                      if k != "extraction"}, indent=2)
    )
    return generate_answer(question, provisions, task="notice_reply")


PROMPT_VERSION = "notice_reply/1"
