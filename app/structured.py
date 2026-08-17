"""
The structured store: clients, deadlines, reconciliation runs, exceptions,
approvals, notice replies.

SQLite via the stdlib — the demo's structured data is a few hundred rows, and a
server database would add an install step for no measurable gain.  Access is
routed through this module only; routes call it inside run_in_threadpool, the
same discipline every other blocking call in the app follows.

Design notes:
- Row payloads (a purchase-register row, a GSTR-2B row) are stored as JSON
  blobs, not normalised into invoice tables.  At 40 rows per register,
  normalisation is pure cost — and the blobs preserve exactly what the
  reconciliation saw, which is what a reviewer wants to look at.
- Runs are immutable history: re-running a period creates a NEW run rather
  than mutating the old one.  The run log (app/runlog.py) lives in files, so
  "append-only" is literally true and the export is one download.
- Approvals are their own table, not status columns, because WHO decided WHAT
  and WHEN is the audit trail — a status column remembers only the outcome.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import cfg

_DDL = """
CREATE TABLE IF NOT EXISTS clients (
    client_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    gstin      TEXT,
    pan        TEXT,
    state_code TEXT
);
CREATE TABLE IF NOT EXISTS deadlines (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL,
    form      TEXT NOT NULL,
    period    TEXT NOT NULL,
    due_date  TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS reco_runs (
    run_id      TEXT PRIMARY KEY,
    client_id   TEXT NOT NULL,
    period      TEXT NOT NULL,
    books_path  TEXT NOT NULL,
    gstr2b_path TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'running',
    stats_json  TEXT NOT NULL DEFAULT '{}',
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    log_path    TEXT
);
CREATE TABLE IF NOT EXISTS reco_matches (
    match_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    books_row_json TEXT,
    g2b_row_json   TEXT,
    match_type     TEXT NOT NULL,
    score          REAL
);
CREATE TABLE IF NOT EXISTS exceptions (
    exc_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    bucket          TEXT NOT NULL,
    books_row_json  TEXT,
    g2b_row_json    TEXT,
    delta_json      TEXT NOT NULL DEFAULT '{}',
    llm_explanation TEXT,
    llm_model       TEXT,
    status          TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    action       TEXT NOT NULL,
    note         TEXT,
    actor        TEXT NOT NULL DEFAULT 'CA (demo)',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notice_replies (
    reply_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_doc_id    TEXT NOT NULL,
    discrepancy_json TEXT NOT NULL,
    draft_md         TEXT NOT NULL,
    sources_json     TEXT NOT NULL DEFAULT '[]',
    model            TEXT,
    prompt_version   TEXT,
    status           TEXT NOT NULL DEFAULT 'draft',
    created_at       TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    """One short-lived connection per operation — SQLite likes it that way."""
    path = Path(cfg.ca_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


# ── Seeding ───────────────────────────────────────────────────────────────────

def seed_from_dataset(seed_dir: str = "data/ca_dataset/seed") -> dict[str, int]:
    """
    Load clients and deadlines from the generated dataset.  Idempotent:
    clients upsert by id, deadlines are replaced wholesale (they are seed
    fixtures, not user data).
    """
    seed = Path(seed_dir)
    counts = {"clients": 0, "deadlines": 0}
    with connect() as conn:
        clients_file = seed / "clients.json"
        if clients_file.exists():
            clients = json.loads(clients_file.read_text(encoding="utf-8"))
            for c in clients:
                conn.execute(
                    "INSERT INTO clients (client_id, name, gstin, pan, state_code) "
                    "VALUES (:client_id, :name, :gstin, :pan, :state_code) "
                    "ON CONFLICT(client_id) DO UPDATE SET name=:name, gstin=:gstin, "
                    "pan=:pan, state_code=:state_code", c)
            counts["clients"] = len(clients)
        deadlines_file = seed / "deadlines.json"
        if deadlines_file.exists():
            deadlines = json.loads(deadlines_file.read_text(encoding="utf-8"))
            conn.execute("DELETE FROM deadlines")
            for d in deadlines:
                conn.execute(
                    "INSERT INTO deadlines (client_id, form, period, due_date, status) "
                    "VALUES (:client_id, :form, :period, :due_date, :status)", d)
            counts["deadlines"] = len(deadlines)
    return counts


def list_clients() -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM clients ORDER BY name")]


def get_client(client_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        return dict(row) if row else None


def list_deadlines(client_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if client_id:
            rows = conn.execute(
                "SELECT * FROM deadlines WHERE client_id = ? ORDER BY due_date",
                (client_id,))
        else:
            rows = conn.execute("SELECT * FROM deadlines ORDER BY due_date")
        return [dict(r) for r in rows]


# ── Reconciliation runs ───────────────────────────────────────────────────────

def create_run(client_id: str, period: str, books_path: str, gstr2b_path: str,
               params: dict | None = None) -> str:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO reco_runs (run_id, client_id, period, books_path, "
            "gstr2b_path, params_json, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
            (run_id, client_id, period, books_path, gstr2b_path,
             json.dumps(params or {}), _now()))
    return run_id


def finish_run(run_id: str, stats: dict, log_path: str | None,
               status: str = "done") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE reco_runs SET status = ?, stats_json = ?, finished_at = ?, "
            "log_path = ? WHERE run_id = ?",
            (status, json.dumps(stats), _now(), log_path, run_id))


def get_run(run_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM reco_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["stats"] = json.loads(d.pop("stats_json") or "{}")
        d["params"] = json.loads(d.pop("params_json") or "{}")
        return d


def list_runs(client_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if client_id:
            rows = conn.execute(
                "SELECT * FROM reco_runs WHERE client_id = ? "
                "ORDER BY started_at DESC", (client_id,))
        else:
            rows = conn.execute(
                "SELECT * FROM reco_runs ORDER BY started_at DESC")
        out = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d.pop("stats_json") or "{}")
            d["params"] = json.loads(d.pop("params_json") or "{}")
            out.append(d)
        return out


def add_matches(run_id: str, matches: list[dict]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO reco_matches (run_id, books_row_json, g2b_row_json, "
            "match_type, score) VALUES (?, ?, ?, ?, ?)",
            [(run_id,
              json.dumps(m.get("books_row")) if m.get("books_row") else None,
              json.dumps(m.get("g2b_row")) if m.get("g2b_row") else None,
              m["match_type"], m.get("score")) for m in matches])


def add_exceptions(run_id: str, exceptions: list[dict]) -> list[int]:
    ids = []
    with connect() as conn:
        for e in exceptions:
            cur = conn.execute(
                "INSERT INTO exceptions (run_id, bucket, books_row_json, "
                "g2b_row_json, delta_json) VALUES (?, ?, ?, ?, ?)",
                (run_id, e["bucket"],
                 json.dumps(e.get("books_row")) if e.get("books_row") else None,
                 json.dumps(e.get("g2b_row")) if e.get("g2b_row") else None,
                 json.dumps(e.get("delta") or {})))
            ids.append(cur.lastrowid)
    return ids


def set_exception_explanation(exc_id: int, explanation: str, model: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE exceptions SET llm_explanation = ?, llm_model = ? "
            "WHERE exc_id = ?", (explanation, model, exc_id))


def list_exceptions(run_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        out = []
        for r in conn.execute(
                "SELECT * FROM exceptions WHERE run_id = ? ORDER BY exc_id",
                (run_id,)):
            d = dict(r)
            d["books_row"] = json.loads(d.pop("books_row_json") or "null")
            d["g2b_row"] = json.loads(d.pop("g2b_row_json") or "null")
            d["delta"] = json.loads(d.pop("delta_json") or "{}")
            out.append(d)
        return out


def decide_exception(exc_id: int, action: str, note: str | None,
                     actor: str = "CA (demo)") -> dict[str, Any] | None:
    """
    Record a review decision.  The decision is an approvals ROW — who, what,
    when — and the exception's status is updated to match.  Statuses:
    accepted | corrected | rejected.
    """
    if action not in ("accepted", "corrected", "rejected"):
        raise ValueError(f"unknown decision action: {action!r}")
    with connect() as conn:
        row = conn.execute(
            "SELECT exc_id FROM exceptions WHERE exc_id = ?", (exc_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE exceptions SET status = ? WHERE exc_id = ?", (action, exc_id))
        conn.execute(
            "INSERT INTO approvals (subject_type, subject_id, action, note, "
            "actor, created_at) VALUES ('exception', ?, ?, ?, ?, ?)",
            (str(exc_id), action, note, actor, _now()))
        updated = conn.execute(
            "SELECT * FROM exceptions WHERE exc_id = ?", (exc_id,)).fetchone()
        d = dict(updated)
        d["books_row"] = json.loads(d.pop("books_row_json") or "null")
        d["g2b_row"] = json.loads(d.pop("g2b_row_json") or "null")
        d["delta"] = json.loads(d.pop("delta_json") or "{}")
        return d


def list_approvals(subject_type: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if subject_type:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE subject_type = ? "
                "ORDER BY approval_id", (subject_type,))
        else:
            rows = conn.execute("SELECT * FROM approvals ORDER BY approval_id")
        return [dict(r) for r in rows]


# ── Notice replies (used by feature 07's casework) ────────────────────────────

def create_reply(notice_doc_id: str, discrepancy: dict, draft_md: str,
                 sources: list[dict], model: str | None,
                 prompt_version: str | None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO notice_replies (notice_doc_id, discrepancy_json, "
            "draft_md, sources_json, model, prompt_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (notice_doc_id, json.dumps(discrepancy), draft_md,
             json.dumps(sources), model, prompt_version, _now()))
        return cur.lastrowid


def get_reply(reply_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM notice_replies WHERE reply_id = ?",
            (reply_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["discrepancy"] = json.loads(d.pop("discrepancy_json"))
        d["sources"] = json.loads(d.pop("sources_json") or "[]")
        return d


def list_replies(notice_doc_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if notice_doc_id:
            rows = conn.execute(
                "SELECT * FROM notice_replies WHERE notice_doc_id = ? "
                "ORDER BY reply_id DESC", (notice_doc_id,))
        else:
            rows = conn.execute(
                "SELECT * FROM notice_replies ORDER BY reply_id DESC")
        out = []
        for r in rows:
            d = dict(r)
            d["discrepancy"] = json.loads(d.pop("discrepancy_json"))
            d["sources"] = json.loads(d.pop("sources_json") or "[]")
            out.append(d)
        return out


def decide_reply(reply_id: int, action: str, note: str | None,
                 actor: str = "CA (demo)") -> dict[str, Any] | None:
    """
    The approval gate.  approve -> 'approved'; request_changes -> back to
    'draft' with the note recorded.  A draft is immutable after approval —
    changing it means creating a new reply row.
    """
    status = {"approve": "approved", "request_changes": "draft"}.get(action)
    if status is None:
        raise ValueError(f"unknown reply action: {action!r}")
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM notice_replies WHERE reply_id = ?",
            (reply_id,)).fetchone()
        if not row:
            return None
        if row["status"] == "approved":
            raise ValueError("reply is already approved and immutable")
        conn.execute(
            "UPDATE notice_replies SET status = ? WHERE reply_id = ?",
            (status, reply_id))
        conn.execute(
            "INSERT INTO approvals (subject_type, subject_id, action, note, "
            "actor, created_at) VALUES ('notice_reply', ?, ?, ?, ?, ?)",
            (str(reply_id), action, note, actor, _now()))
    return get_reply(reply_id)
