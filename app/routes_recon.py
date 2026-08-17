"""
Reconciliation API — the recon "agent" as HTTP routes.

POST /recon/run streams the pipeline's stage events over SSE using the exact
frame contract /query/stream established (event: <name> / data: <json>), so
the frontend's existing SSE parser consumes it unchanged.  The stages ARE the
agent-activity trace the UI renders: load → verify → match → classify →
explain → done, each with real counts, none invented.

The pipeline itself is deterministic (app/recon.py); the one LLM step —
explaining each exception — is a bounded, per-exception call whose failure
degrades to the deterministic bucket description, never to an error.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import recon, structured
from app.config import cfg
from app.generation import generate_answer
from app.models import RetrievedChunk, Chunk, ChunkMetadata
from app.runlog import RunLog

log = logging.getLogger(__name__)

router = APIRouter(prefix="/recon", tags=["reconciliation"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ── Request/response models ───────────────────────────────────────────────────

class ReconRunRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    period: str = Field(..., min_length=4)


class DecisionRequest(BaseModel):
    action: str = Field(..., description="accepted | corrected | rejected")
    note: str | None = None


# ── Dataset discovery ─────────────────────────────────────────────────────────

def _dataset_paths(client_id: str, period: str) -> tuple[Path, Path]:
    """
    The demo's structured inputs live in the committed dataset, named by
    client and period.  A missing pair is a 404 with a message that names
    what was looked for, not a stack trace.
    """
    base = Path("data/ca_dataset")
    books = base / f"{client_id}_purchase_register_{period}.csv"
    g2b = base / f"{client_id}_gstr2b_{period}.csv"
    if not books.exists() or not g2b.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No purchase register / GSTR-2B pair found for client "
                   f"'{client_id}', period {period}.")
    return books, g2b


# ── LLM explanation of one exception ─────────────────────────────────────────

def _explain_exception(exc: dict) -> tuple[str, str]:
    """
    One bounded generation call.  The exception's rows and delta go in as a
    pseudo-chunk so the existing provider/retry/fallback machinery applies
    unchanged.  Returns (explanation_text, model_or_'deterministic').

    If the provider fails or returns something unparseable, the DETERMINISTIC
    bucket description is stored instead — a wrong-but-fluent explanation is
    worse than a plain factual one.
    """
    fallback = _deterministic_explanation(exc)

    payload = json.dumps({
        "bucket": exc["bucket"],
        "books_row": exc.get("books_row"),
        "gstr2b_row": exc.get("g2b_row"),
        "delta": exc.get("delta"),
    }, indent=2)
    pseudo = RetrievedChunk(chunk=Chunk(
        chunk_id="recon_exception",
        text=payload,
        metadata=ChunkMetadata(
            chunk_id="recon_exception", doc_id="recon", doc_name="Reconciliation",
            page_number=1, doc_type="register",
        ),
    ))
    try:
        answer, source = generate_answer(
            "Explain this reconciliation exception.", [pseudo],
            task="recon_explain")
        if source != "generated":
            return fallback, "deterministic"
        parsed = json.loads(answer.strip().strip("`").removeprefix("json"))
        cause = str(parsed.get("cause") or "").strip()
        action = str(parsed.get("recommended_action") or "").strip()
        if not cause:
            return fallback, "deterministic"
        text = cause + (f"\n\nRecommended action: {action}" if action else "")
        return text, cfg.generation_model
    except Exception as e:  # noqa: BLE001 — explanation must degrade, never raise
        log.warning("exception explanation failed (%s); using deterministic text", e)
        return fallback, "deterministic"


def _deterministic_explanation(exc: dict) -> str:
    """Plain factual description per bucket — no model involved."""
    d = exc.get("delta") or {}
    bucket = exc["bucket"]
    if bucket == "in_books_not_2b":
        return ("Recorded in the purchase register but absent from GSTR-2B — "
                "typically the supplier has not filed GSTR-1 for the period, so "
                "the credit fails the condition in section 16(2)(aa) until it "
                f"appears. ITC not yet available: Rs. {d.get('itc_not_yet_available', 0):,.2f}.")
    if bucket == "in_2b_not_books":
        return ("Present in GSTR-2B but not recorded in the purchase register — "
                "either an unrecorded purchase or a credit that does not belong "
                f"to this business. Unclaimed credit: Rs. {d.get('credit_unclaimed', 0):,.2f}.")
    if bucket == "amount_mismatch":
        return ("Same invoice, different taxable value: books "
                f"Rs. {d.get('taxable_value_books', 0):,.2f} vs GSTR-2B "
                f"Rs. {d.get('taxable_value_2b', 0):,.2f} "
                f"(delta Rs. {d.get('delta_taxable', 0):,.2f}).")
    if bucket == "gstin_mismatch":
        return ("Invoice and value agree but the GSTIN differs: books show "
                f"{d.get('gstin_books')}, GSTR-2B shows {d.get('gstin_2b')} — "
                "one of the two registrations is wrong.")
    if bucket == "duplicate_in_books":
        return ("The same invoice is recorded twice in the purchase register; "
                "claiming it twice would double the input tax credit "
                f"(Rs. {d.get('itc_at_risk', 0):,.2f} at risk).")
    return "Exception requires review."


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_reconciliation(req: ReconRunRequest):
    """The recon agent, streamed stage by stage."""
    client = await run_in_threadpool(structured.get_client, req.client_id)
    if not client:
        raise HTTPException(status_code=404,
                            detail=f"Unknown client '{req.client_id}'.")
    books_path, g2b_path = _dataset_paths(req.client_id, req.period)

    async def stages():
        run_id = await run_in_threadpool(
            structured.create_run, req.client_id, req.period,
            str(books_path), str(g2b_path))
        runlog = RunLog(run_id)
        try:
            yield _sse("stage", {"run_id": run_id, "stage": "load",
                                 "state": "running"})
            books = await run_in_threadpool(recon.load_register, str(books_path))
            g2b = await run_in_threadpool(recon.load_gstr2b, str(g2b_path))
            runlog.event("load", f"loaded {books_path.name} and {g2b_path.name}",
                         counts={"books_rows": len(books), "gstr2b_rows": len(g2b)})
            yield _sse("stage", {"run_id": run_id, "stage": "load",
                                 "state": "done",
                                 "counts": {"books_rows": len(books),
                                            "gstr2b_rows": len(g2b)}})

            yield _sse("stage", {"run_id": run_id, "stage": "match",
                                 "state": "running"})
            result = await run_in_threadpool(recon.reconcile, books, g2b)
            runlog.event("verify", "deterministic checks",
                         counts=result.stats["checks"])
            runlog.event("match", "exact + fuzzy + tolerance matching",
                         counts={"exact": result.stats["exact_matches"],
                                 "amended": result.stats["amendment_resolved"],
                                 "fuzzy": result.stats["fuzzy_matches"]})
            runlog.event("classify", "exceptions bucketed",
                         counts=result.stats["buckets"])
            yield _sse("stage", {"run_id": run_id, "stage": "match",
                                 "state": "done",
                                 "counts": {"matched": len(result.matches)}})
            yield _sse("stage", {"run_id": run_id, "stage": "classify",
                                 "state": "done",
                                 "counts": result.stats["buckets"]})

            await run_in_threadpool(structured.add_matches, run_id, result.matches)
            exc_ids = await run_in_threadpool(
                structured.add_exceptions, run_id, result.exceptions)

            # One bounded LLM call per exception; failure degrades to the
            # deterministic text. Progress streams per item so the trace moves.
            yield _sse("stage", {"run_id": run_id, "stage": "explain",
                                 "state": "running",
                                 "counts": {"total": len(exc_ids)}})
            for i, (exc_id, exc) in enumerate(zip(exc_ids, result.exceptions), 1):
                text, model = await run_in_threadpool(_explain_exception, exc)
                await run_in_threadpool(
                    structured.set_exception_explanation, exc_id, text, model)
                runlog.event("explain", f"exception {exc_id} ({exc['bucket']})",
                             model=model, prompt_version="recon_explain")
                yield _sse("stage", {"run_id": run_id, "stage": "explain",
                                     "state": "running",
                                     "counts": {"done": i, "total": len(exc_ids)}})
            yield _sse("stage", {"run_id": run_id, "stage": "explain",
                                 "state": "done",
                                 "counts": {"total": len(exc_ids)}})

            await run_in_threadpool(
                structured.finish_run, run_id, result.stats, str(runlog.path))
            runlog.event("done", "run complete", counts=result.stats["buckets"])
            yield _sse("done", {"run_id": run_id, "stats": result.stats})
        except Exception as e:  # noqa: BLE001 — the stream must end, not hang
            log.exception("reconciliation run failed")
            await run_in_threadpool(
                structured.finish_run, run_id, {}, str(runlog.path), "failed")
            runlog.event("error", str(e))
            yield _sse("error", {"run_id": run_id,
                                 "message": "Reconciliation failed. See the run log."})

    return StreamingResponse(stages(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/runs")
async def get_runs(client_id: str | None = None):
    runs = await run_in_threadpool(structured.list_runs, client_id)
    return {"runs": runs, "total": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = await run_in_threadpool(structured.get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run '{run_id}'.")
    return run


@router.get("/runs/{run_id}/exceptions")
async def get_exceptions(run_id: str):
    run = await run_in_threadpool(structured.get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run '{run_id}'.")
    exceptions = await run_in_threadpool(structured.list_exceptions, run_id)
    return {"run_id": run_id, "exceptions": exceptions,
            "total": len(exceptions)}


@router.get("/runs/{run_id}/log")
async def get_run_log(run_id: str):
    """The append-only run log, served as-is — the working-paper export."""
    run = await run_in_threadpool(structured.get_run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Unknown run '{run_id}'.")
    text = RunLog(run_id).read_text()
    if not text:
        raise HTTPException(status_code=404,
                            detail=f"No log recorded for run '{run_id}'.")
    return PlainTextResponse(
        text, media_type="application/x-ndjson",
        headers={"Content-Disposition":
                 f'attachment; filename="{run_id}.jsonl"'})


@router.post("/exceptions/{exc_id}/decision")
async def decide(exc_id: int, req: DecisionRequest):
    """
    The human gate: accept / correct / reject, recorded as an approvals row —
    who decided what, when — not just a status flip.
    """
    try:
        updated = await run_in_threadpool(
            structured.decide_exception, exc_id, req.action, req.note)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404,
                            detail=f"Unknown exception {exc_id}.")
    return updated


@router.get("/clients")
async def get_clients():
    clients = await run_in_threadpool(structured.list_clients)
    return {"clients": clients, "total": len(clients)}
