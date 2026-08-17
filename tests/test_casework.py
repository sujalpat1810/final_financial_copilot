"""
Casework tests: extraction (model path and regex fallback), the pinned-statute
retrieval contract, the analyze pipeline end-to-end with generation stubbed,
and the approval gate's semantics — including immutability after approval.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.casework as casework
from app.config import cfg
from app.main import app

DATASET = Path("data/ca_dataset")

pytestmark = pytest.mark.skipif(
    not (DATASET / "asmt10_mehta.pdf").exists(),
    reason="dataset not generated")


NOTICE_TEXT = """FORM GST ASMT-10
[See rule 99(1)] Notice for intimating discrepancies in the return after scrutiny
Reference no.: ZD2708260012345X Date: 2026-08-05
To: Mehta Textiles Pvt Ltd
GSTIN: 27AAECM1201F1ZE
Tax period: December 2025 (FY 2025-26) Return: GSTR-3B
input tax credit of Rs. 38,904.00 availed in Table 4(A)(5) of FORM GSTR-3B
under section 61 of the CGST Act read with rule 99, proceedings under
section 73 or section 74. Reply within thirty days in FORM GST ASMT-11.
"""


# ── Regex fallback ────────────────────────────────────────────────────────────

def test_regex_extraction_reads_particulars_literally():
    d = casework._regex_extract(NOTICE_TEXT)
    assert d["amount"] == 38904.00
    assert "December 2025" in d["period"]
    assert d["reference_no"] == "ZD2708260012345X"
    assert d["gstin"] == "27AAECM1201F1ZE"
    assert d["notice_type"] == "ASMT-10"
    assert d["reply_form"] == "ASMT-11"
    assert d["reply_due_days"] == 30
    assert "section 61" in d["sections_cited"]
    assert d["extraction"] == "regex"


def test_model_amount_disagreeing_with_literal_is_overruled(monkeypatch):
    """The regex read the amount off the notice; a model that 'reads' a
    different number loses to the literal one."""
    monkeypatch.setattr(casework, "notice_text", lambda doc_id: NOTICE_TEXT)
    monkeypatch.setattr(
        casework, "generate_answer",
        lambda q, chunks, task: (json.dumps({
            "notice_type": "ASMT-10", "alleged_discrepancy": "ITC excess",
            "amount": 99999.0, "period": "December 2025",
        }), "generated"))
    d = casework.extract_discrepancy("whatever")
    assert d["amount"] == 38904.00
    assert d["extraction"] == "generated"


def test_extraction_falls_back_on_unparseable_model_output(monkeypatch):
    monkeypatch.setattr(casework, "notice_text", lambda doc_id: NOTICE_TEXT)
    monkeypatch.setattr(casework, "generate_answer",
                        lambda q, chunks, task: ("not json at all", "generated"))
    d = casework.extract_discrepancy("whatever")
    assert d["extraction"] == "regex"
    assert d["amount"] == 38904.00


# ── Pipeline over HTTP ────────────────────────────────────────────────────────

DRAFT = """## Legal Header
Reference ZD2708260012345X, GSTIN 27AAECM1201F1ZE, December 2025.

## Statement of Facts
The notice alleges excess ITC of Rs. 38,904.00 [Page 1].

## Point-wise Submissions
1. Scrutiny is governed by section 61(1) [Page 1].

## Prayer
Proceedings be dropped via FORM GST ASMT-12 [Page 1].

## Request for Personal Hearing
A personal hearing is requested under section 75(4) [Page 1].
"""


@pytest.fixture(scope="module")
def _app_client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client(_app_client, monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "ca_db_path", str(tmp_path / "ca_demo.db"))
    import app.runlog as runlog
    monkeypatch.setattr(runlog, "RUNS_DIR", tmp_path / "runs")
    # Both LLM steps stubbed: extraction returns structured particulars, the
    # draft returns the fixed skeleton. Retrieval runs for real against the
    # indexed statutes.
    def fake_generate(question, chunks, task="default"):
        if task == "notice_extract":
            return (json.dumps({
                "notice_type": "ASMT-10", "reference_no": "ZD2708260012345X",
                "gstin": "27AAECM1201F1ZE", "period": "December 2025",
                "alleged_discrepancy": "Excess ITC vs GSTR-2B",
                "amount": 38904.0, "sections_cited": ["section 61"],
                "reply_form": "ASMT-11", "reply_due_days": 30,
            }), "generated")
        return (DRAFT, "generated")
    monkeypatch.setattr(casework, "generate_answer", fake_generate)
    yield _app_client


def _notice_id(client) -> str:
    notices = client.get("/notices").json()["notices"]
    assert notices, "no notice in the corpus"
    return notices[0]["doc_id"]


def _analyze(client, doc_id):
    events = []
    with client.stream("POST", f"/notices/{doc_id}/analyze") as r:
        assert r.status_code == 200
        current = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                current = line[7:]
            elif line.startswith("data: "):
                events.append((current, json.loads(line[6:])))
    return events


def test_analyze_streams_stages_and_persists_a_draft(client):
    events = _analyze(client, _notice_id(client))
    stages = [d["stage"] for e, d in events if e == "stage"]
    for expected in ("extract", "retrieve", "draft"):
        assert expected in stages
    disc = next(d for e, d in events if e == "discrepancy")
    assert disc["amount"] == 38904.0
    done = next(d for e, d in events if e == "done")
    reply = done["reply"]
    assert reply["status"] == "draft"
    assert "## Point-wise Submissions" in reply["draft_md"]
    # Retrieval was pinned to statutes: every source is a statute chunk.
    assert reply["sources"], "no provisions retrieved"
    assert all(s["doc_type"] == "statute" for s in reply["sources"])


def test_approval_gate_locks_the_reply(client):
    events = _analyze(client, _notice_id(client))
    reply = next(d for e, d in events if e == "done")["reply"]

    approved = client.post(f"/notices/replies/{reply['reply_id']}/decision",
                           json={"action": "approve", "note": "ok"}).json()
    assert approved["status"] == "approved"

    # Approved replies are immutable — any further decision is refused.
    again = client.post(f"/notices/replies/{reply['reply_id']}/decision",
                        json={"action": "request_changes"})
    assert again.status_code == 422

    from app.structured import list_approvals
    acts = [a for a in list_approvals("notice_reply")]
    assert acts and acts[-1]["action"] == "approve"


def test_request_changes_returns_to_draft(client):
    events = _analyze(client, _notice_id(client))
    reply = next(d for e, d in events if e == "done")["reply"]
    updated = client.post(f"/notices/replies/{reply['reply_id']}/decision",
                          json={"action": "request_changes",
                                "note": "cite rule 99(2) as well"}).json()
    assert updated["status"] == "draft"


def test_notices_list_carries_latest_reply_status(client):
    doc_id = _notice_id(client)
    events = _analyze(client, doc_id)
    reply = next(d for e, d in events if e == "done")["reply"]
    client.post(f"/notices/replies/{reply['reply_id']}/decision",
                json={"action": "approve"})
    notices = client.get("/notices").json()["notices"]
    entry = next(n for n in notices if n["doc_id"] == doc_id)
    assert entry["latest_reply_status"] == "approved"
    assert entry["latest_reply_id"] == reply["reply_id"]


def test_unknown_notice_is_404(client):
    r = client.post("/notices/0000000000000000/analyze")
    assert r.status_code == 404
