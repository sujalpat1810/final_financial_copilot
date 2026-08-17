"""
Notice casework API.

POST /notices/{doc_id}/analyze streams the pipeline over SSE with the same
frame contract as /recon/run — stages extract → retrieve → draft, then done
carrying the persisted reply.  The reply is created as status='draft' and can
only leave that state through the approval gate, which records WHO decided
WHAT and WHEN as an approvals row.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import casework, structured
from app.config import cfg
from app.ingestion import list_documents
from app.runlog import RunLog

log = logging.getLogger(__name__)

router = APIRouter(prefix="/notices", tags=["notices"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class ReplyDecisionRequest(BaseModel):
    action: str = Field(..., description="approve | request_changes")
    note: str | None = None


def _citations(provisions) -> list[dict]:
    """Source dicts shaped like SourceCitation, so the frontend's existing
    citation chips and viewer work unchanged."""
    from app.confidence import relevance
    out = []
    for r in provisions:
        m = r.chunk.metadata
        out.append({
            "doc_name": m.doc_name, "page_number": m.page_number,
            "section_title": m.section_title, "fiscal_year": m.fiscal_year,
            "doc_id": m.doc_id, "chunk_id": m.chunk_id,
            "entity": m.entity, "basis": m.basis, "client": m.client,
            "doc_type": m.doc_type, "act_version": m.act_version,
            "rerank_score": r.rerank_score,
            "relevance": relevance(r.rerank_score),
            "is_table": "[TABLE]" in r.chunk.text,
            "excerpt": r.chunk.text[:600],
        })
    return out


@router.get("")
async def get_notices():
    """Indexed notices, each with its latest reply's id and status."""
    docs = await run_in_threadpool(list_documents)
    notices = [d for d in docs if d.doc_type == "notice"]
    replies = await run_in_threadpool(structured.list_replies)
    latest: dict[str, dict] = {}
    for r in replies:                       # list is newest-first
        latest.setdefault(r["notice_doc_id"], r)
    return {"notices": [{
        **d.model_dump(),
        "latest_reply_id": latest.get(d.doc_id, {}).get("reply_id"),
        "latest_reply_status": latest.get(d.doc_id, {}).get("status"),
    } for d in notices], "total": len(notices)}


@router.post("/{doc_id}/analyze")
async def analyze(doc_id: str):
    """The casework pipeline, streamed stage by stage."""
    text = await run_in_threadpool(casework.notice_text, doc_id)
    if not text:
        raise HTTPException(status_code=404,
                            detail=f"No indexed notice with id '{doc_id}'.")

    # Deferred import: main imports this router at module load, so importing
    # main's state at module level here would be circular.
    from app.main import _state
    retriever = _state.get("retriever")
    if retriever is None:
        raise HTTPException(status_code=503, detail="Retrieval is still starting up.")

    async def stages():
        runlog = RunLog(f"notice_{doc_id}")
        try:
            yield _sse("stage", {"stage": "extract", "state": "running"})
            discrepancy = await run_in_threadpool(
                casework.extract_discrepancy, doc_id)
            if not discrepancy:
                yield _sse("error", {"message": "The notice text could not be read."})
                return
            runlog.event("extract", "notice particulars",
                         model=cfg.generation_model
                         if discrepancy.get("extraction") == "generated"
                         else "regex",
                         prompt_version="notice_extract")
            yield _sse("stage", {"stage": "extract", "state": "done"})
            yield _sse("discrepancy", discrepancy)

            yield _sse("stage", {"stage": "retrieve", "state": "running"})
            provisions = await run_in_threadpool(
                casework.retrieve_provisions, retriever, discrepancy)
            runlog.event("retrieve", "statute provisions",
                         counts={"provisions": len(provisions)})
            yield _sse("stage", {"stage": "retrieve", "state": "done",
                                 "counts": {"provisions": len(provisions)}})

            yield _sse("stage", {"stage": "draft", "state": "running"})
            draft, source = await run_in_threadpool(
                casework.draft_reply, discrepancy, provisions)
            sources = _citations(provisions)
            if source != "generated":
                # No provider. The honest degraded mode: discrepancy +
                # provisions, clearly labelled, no fabricated prose.
                draft = ("*(No generated draft is available. The extracted "
                         "discrepancy and the governing provisions are shown "
                         "for the CA to draft from.)*\n\n" + draft)
            reply_id = await run_in_threadpool(
                structured.create_reply, doc_id, discrepancy, draft, sources,
                cfg.generation_model if source == "generated" else None,
                casework.PROMPT_VERSION)
            runlog.event("draft", f"reply {reply_id} ({source})",
                         model=cfg.generation_model if source == "generated" else None,
                         prompt_version=casework.PROMPT_VERSION)
            yield _sse("stage", {"stage": "draft", "state": "done"})
            reply = await run_in_threadpool(structured.get_reply, reply_id)
            yield _sse("done", {"reply": reply, "answer_source": source})
        except Exception as e:  # noqa: BLE001 — the stream must end, not hang
            log.exception("notice analysis failed")
            runlog.event("error", str(e))
            yield _sse("error", {"message": "Analysis failed. See the server log."})

    return StreamingResponse(stages(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/replies/{reply_id}")
async def get_reply(reply_id: int):
    reply = await run_in_threadpool(structured.get_reply, reply_id)
    if not reply:
        raise HTTPException(status_code=404, detail=f"Unknown reply {reply_id}.")
    return reply


@router.post("/replies/{reply_id}/decision")
async def decide_reply(reply_id: int, req: ReplyDecisionRequest):
    """The approval gate: approve, or send back for changes. Approved replies
    are immutable — a change after approval is a NEW draft."""
    try:
        updated = await run_in_threadpool(
            structured.decide_reply, reply_id, req.action, req.note)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail=f"Unknown reply {reply_id}.")
    return updated
