"""
Contract tests for the reconciliation routes.

Generation is stubbed (the explanation step must not need torch or an API key
to test), the SQLite store is redirected to tmp_path, and the run streams
against the real committed dataset — so these tests exercise the same
end-to-end path the demo walks, minus the LLM.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.routes_recon as routes_recon
from app.config import cfg
from app.main import app

DATASET = Path("data/ca_dataset")

pytestmark = pytest.mark.skipif(
    not (DATASET / "mehta_purchase_register_2025-12.csv").exists(),
    reason="dataset not generated")


@pytest.fixture(scope="module")
def _app_client():
    """One TestClient for the module — the lifespan loads the embedding and
    reranker models, ~20 s, which per-test isolation would pay eight times."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client(_app_client, monkeypatch, tmp_path):
    # Isolated structured store per test; runs dir redirected too.
    monkeypatch.setattr(cfg, "ca_db_path", str(tmp_path / "ca_demo.db"))
    import app.runlog as runlog
    monkeypatch.setattr(runlog, "RUNS_DIR", tmp_path / "runs")

    # The explanation step is covered by its own unit below; endpoint tests
    # use the deterministic text so no provider is touched.
    monkeypatch.setattr(
        routes_recon, "_explain_exception",
        lambda exc: (routes_recon._deterministic_explanation(exc), "deterministic"))

    from app.structured import seed_from_dataset
    seed_from_dataset()
    yield _app_client


def _run(client) -> dict:
    """POST /recon/run and parse the SSE stream into events."""
    events = []
    with client.stream("POST", "/recon/run",
                       json={"client_id": "mehta", "period": "2025-12"}) as r:
        assert r.status_code == 200
        current = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                current = line[7:]
            elif line.startswith("data: "):
                events.append((current, json.loads(line[6:])))
    return {"events": events,
            "done": next((d for e, d in events if e == "done"), None)}


def test_run_streams_stages_and_completes(client):
    out = _run(client)
    stages = [d["stage"] for e, d in out["events"] if e == "stage"]
    for expected in ("load", "match", "classify", "explain"):
        assert expected in stages, f"stage {expected} missing"
    assert out["done"], "no terminal done event"
    assert out["done"]["stats"]["buckets"]["in_books_not_2b"] == 4


def test_run_persists_exceptions_and_log(client):
    out = _run(client)
    run_id = out["done"]["run_id"]

    body = client.get(f"/recon/runs/{run_id}/exceptions").json()
    assert body["total"] == out["done"]["stats"]["exceptions_total"]
    # Every exception got an explanation before the run finished.
    assert all(e["llm_explanation"] for e in body["exceptions"])

    log_response = client.get(f"/recon/runs/{run_id}/log")
    assert log_response.status_code == 200
    lines = [json.loads(l) for l in log_response.text.splitlines()]
    assert lines[0]["stage"] == "load"
    assert lines[-1]["stage"] == "done"


def test_decision_records_an_approval(client):
    out = _run(client)
    run_id = out["done"]["run_id"]
    exc = client.get(f"/recon/runs/{run_id}/exceptions").json()["exceptions"][0]

    updated = client.post(f"/recon/exceptions/{exc['exc_id']}/decision",
                          json={"action": "accepted", "note": "verified"}).json()
    assert updated["status"] == "accepted"

    from app.structured import list_approvals
    approvals = list_approvals("exception")
    assert len(approvals) == 1
    assert approvals[0]["action"] == "accepted"
    assert approvals[0]["note"] == "verified"
    assert approvals[0]["actor"]


def test_invalid_decision_action_is_422(client):
    out = _run(client)
    run_id = out["done"]["run_id"]
    exc = client.get(f"/recon/runs/{run_id}/exceptions").json()["exceptions"][0]
    r = client.post(f"/recon/exceptions/{exc['exc_id']}/decision",
                    json={"action": "maybe"})
    assert r.status_code == 422


def test_reruns_create_new_immutable_runs(client):
    first = _run(client)["done"]["run_id"]
    second = _run(client)["done"]["run_id"]
    assert first != second
    runs = client.get("/recon/runs").json()
    assert runs["total"] == 2


def test_unknown_client_is_404(client):
    r = client.post("/recon/run",
                    json={"client_id": "nobody", "period": "2025-12"})
    assert r.status_code == 404


def test_unknown_period_is_404_naming_what_was_sought(client):
    r = client.post("/recon/run",
                    json={"client_id": "mehta", "period": "2019-01"})
    assert r.status_code == 404
    assert "2019-01" in r.json()["detail"]


def test_clients_endpoint_lists_seeded_clients(client):
    body = client.get("/recon/clients").json()
    names = {c["name"] for c in body["clients"]}
    assert "Mehta Textiles Pvt Ltd" in names


def test_deterministic_explanations_cover_every_bucket():
    from app.recon import BUCKETS
    for bucket in BUCKETS:
        text = routes_recon._deterministic_explanation(
            {"bucket": bucket, "delta": {}})
        assert len(text) > 30, bucket
