"""
The prompt's job is to stop one failure: a figure stated without its entity,
fiscal year and basis. These tests pin the parts of it that carry that weight,
plus the vendor-neutrality of everything the user can see.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import generation, main
from app.config import cfg
from app.generation import _build_prompt, _source_label, generate_answer
from app.models import Chunk, ChunkMetadata, RetrievedChunk

VENDOR_WORDS = ("gemini", "google", "genai", "api key", "api_key", "openai", "llm")


def _rc(page=276, text="Revenue from operations 162,990 153,670",
        entity="Infosys", fiscal_year="FY2024-25", basis="consolidated",
        section=None, score=6.4) -> RetrievedChunk:
    chunk_id = f"doc123_p{page}_0"
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=chunk_id,
            text=text,
            metadata=ChunkMetadata(
                chunk_id=chunk_id, doc_id="doc123", doc_name="Infosys FY2024-25",
                page_number=page, entity=entity, fiscal_year=fiscal_year,
                basis=basis, section_title=section,
            ),
        ),
        rerank_score=score,
    )


# ── Source labels ─────────────────────────────────────────────────────────────

def test_label_carries_all_four_provenance_fields():
    label = _source_label(_rc(), 1)
    assert "Infosys" in label
    assert "FY2024-25" in label
    assert "Consolidated" in label
    assert "Page 276" in label


def test_undetermined_basis_is_named_not_omitted():
    """
    A named unknown is something the model can report. A silently missing field
    reads as an oversight it may paper over with an assumption.
    """
    assert "Basis not determined" in _source_label(_rc(basis=None), 1)


def test_missing_entity_and_year_are_named_too():
    label = _source_label(_rc(entity=None, fiscal_year=None), 1)
    assert "Entity not recorded" in label
    assert "Fiscal year not recorded" in label


def test_section_title_is_included_when_present():
    assert "Statement of Profit" in _source_label(
        _rc(section="Consolidated Statement of Profit and Loss"), 1)


# ── Prompt content ────────────────────────────────────────────────────────────

def test_prompt_requires_every_figure_to_be_qualified():
    prompt = _build_prompt("What was revenue?", [_rc()])
    assert "entity, fiscal year and basis" in prompt


def test_prompt_separates_report_year_from_figure_year():
    """
    The distinction the whole restated acceptance criterion rests on: the label's
    year is the report's, the column header's year is the number's.
    """
    prompt = _build_prompt("q", [_rc()])
    assert "THE REPORT'S YEAR IS NOT THE FIGURE'S YEAR" in prompt
    assert "comparative column" in prompt


def test_prompt_forbids_silently_picking_one_answer():
    prompt = _build_prompt("What was revenue?", [_rc()])
    assert "Never silently pick one" in prompt


def test_prompt_forbids_inferring_basis():
    prompt = _build_prompt("q", [_rc(basis=None)])
    assert "Never infer it" in prompt


def test_prompt_includes_every_chunk_and_numbers_them():
    prompt = _build_prompt("q", [_rc(page=276), _rc(page=196, basis="standalone")])
    assert "[Source 1]" in prompt and "[Source 2]" in prompt
    assert "Page 276" in prompt and "Page 196" in prompt


def test_prompt_carries_full_chunk_text_not_an_excerpt():
    """The model must see the whole chunk; truncation is a display concern."""
    long_text = "Revenue " + ("x" * 3000)
    assert long_text in _build_prompt("q", [_rc(text=long_text)])


# ── answer_source is vendor neutral ───────────────────────────────────────────

def test_no_key_yields_extractive(monkeypatch):
    monkeypatch.setattr(cfg, "gemini_api_key", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    answer, source = generate_answer("q", [_rc()])

    assert source == "extractive"
    assert answer


def _stub_sdk(monkeypatch, side_effects):
    """
    Replace google.genai.Client with one that replays `side_effects` per call.

    An entry that is an Exception is raised, anything else is returned as the
    response text.  Returns the call log so a test can assert how many attempts
    were made.

    Stubbed rather than left to fail naturally: google-genai IS installed now, so
    an unstubbed call would hit the network, burn quota, and — since transient
    failures retry — do it three times with backoff.
    """
    from google import genai

    calls = []

    class _Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            effect = side_effects[min(len(calls) - 1, len(side_effects) - 1)]
            if isinstance(effect, Exception):
                raise effect
            return type("R", (), {"text": effect})()

    class _Client:
        def __init__(self, **_kwargs):
            self.models = _Models()

    monkeypatch.setattr(genai, "Client", _Client)
    # Keep the suite fast: the backoff is real time, and correctness here is
    # about the number of attempts, not the length of the pauses.
    monkeypatch.setattr(generation, "_BACKOFF_SECONDS", 0.0)
    return calls


def test_generation_failure_falls_back_without_leaking_the_error(monkeypatch):
    """
    The exception can carry the model name and account details. It belongs in the
    log, not in the answer the user reads.
    """
    monkeypatch.setattr(cfg, "gemini_api_key", "sk-not-a-real-key")
    _stub_sdk(monkeypatch, [RuntimeError("404 model gemini-x not found for project acme")])

    answer, source = generate_answer("q", [_rc()])

    assert source == "extractive"
    lowered = answer.lower()
    for word in VENDOR_WORDS:
        assert word not in lowered, f"{word!r} leaked into the answer"
    assert "acme" not in lowered and "404" not in answer


# ── Transient failures retry; permanent ones do not ───────────────────────────

def test_transient_failure_is_retried_and_can_succeed(monkeypatch):
    """
    A 503 "high demand" blip cost a real answer during verification. The fallback
    was correct, but the reader cannot tell that degraded answer apart from one
    where the evidence was genuinely thin — so a blip should not produce one.
    """
    monkeypatch.setattr(cfg, "gemini_api_key", "k")
    calls = _stub_sdk(monkeypatch, [
        RuntimeError("503 UNAVAILABLE. model is currently experiencing high demand"),
        "the generated answer",
    ])

    answer, source = generate_answer("q", [_rc()])

    assert source == "generated"
    assert answer == "the generated answer"
    assert len(calls) == 2


def test_transient_failure_gives_up_after_the_attempt_budget(monkeypatch):
    monkeypatch.setattr(cfg, "gemini_api_key", "k")
    calls = _stub_sdk(monkeypatch, [RuntimeError("503 UNAVAILABLE")])

    _, source = generate_answer("q", [_rc()])

    assert source == "extractive"
    assert len(calls) == generation._MAX_ATTEMPTS


@pytest.mark.parametrize("message", [
    "404 NOT_FOUND. model is no longer available to new users",
    "401 UNAUTHENTICATED. API key not valid",
    "403 PERMISSION_DENIED",
    "400 INVALID_ARGUMENT",
])
def test_permanent_failure_is_not_retried(monkeypatch, message):
    """
    A retired model or a bad key fails identically three times. Retrying only
    adds latency to a failure that was never going to succeed.
    """
    monkeypatch.setattr(cfg, "gemini_api_key", "k")
    calls = _stub_sdk(monkeypatch, [RuntimeError(message)])

    _, source = generate_answer("q", [_rc()])

    assert source == "extractive"
    assert len(calls) == 1, f"retried a permanent error: {message}"


@pytest.mark.parametrize("message,transient", [
    ("503 UNAVAILABLE", True),
    ("429 RESOURCE_EXHAUSTED", True),
    ("500 internal error", True),
    ("504 DEADLINE_EXCEEDED", True),
    ("404 NOT_FOUND", False),
    ("401 UNAUTHENTICATED", False),
])
def test_transient_classification(message, transient):
    assert generation._is_transient(RuntimeError(message)) is transient


# ── A daily quota is not a transient failure ──────────────────────────────────

DAILY_429 = (
    "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
    "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
    "limit: 20 ... quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
)


def test_daily_quota_is_not_retried():
    """
    Observed on a free-tier key capped at 20 requests a day: three attempts spent
    three of the remaining allowance on a window that only reopens tomorrow. No
    backoff reaches it, so retrying is pure waste.
    """
    assert generation._is_daily_quota(RuntimeError(DAILY_429)) is True
    assert generation._is_transient(RuntimeError(DAILY_429)) is False


def test_per_minute_429_is_still_retried():
    """The common burst limit must keep its retry — only the daily cap is fatal."""
    burst = "429 RESOURCE_EXHAUSTED. Please retry in 22s."
    assert generation._is_daily_quota(RuntimeError(burst)) is False
    assert generation._is_transient(RuntimeError(burst)) is True


def test_exhausted_quota_costs_exactly_one_request(monkeypatch):
    monkeypatch.setattr(cfg, "gemini_api_key", "k")
    calls = _stub_sdk(monkeypatch, [RuntimeError(DAILY_429)])

    _, source = generate_answer("q", [_rc()])

    assert source == "extractive"
    assert len(calls) == 1, "retried a quota that does not reset until tomorrow"


def test_extractive_answer_carries_provenance(monkeypatch):
    """An extractive answer is still figures that must not be read unqualified."""
    monkeypatch.setattr(cfg, "gemini_api_key", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    answer, _ = generate_answer("q", [_rc(), _rc(page=196, basis="standalone")])

    assert "Infosys" in answer
    assert "Consolidated" in answer and "Standalone" in answer
    assert "Page 276" in answer and "Page 196" in answer


def test_no_chunks_is_extractive_not_a_crash():
    answer, source = generate_answer("q", [])
    assert source == "extractive"
    assert answer


# ── Nothing names the vendor where a client can see it ────────────────────────

def test_health_has_no_vendor_field():
    client = TestClient(main.app)
    body = client.get("/health").json()

    assert "generation_available" in body
    assert "gemini_available" not in body

    payload = str(body).lower()
    for word in ("gemini", "google", "genai"):
        assert word not in payload, f"{word!r} appears in the /health payload"


def test_openapi_schema_names_no_vendor():
    """/docs is public, so the schema is client-visible surface."""
    client = TestClient(main.app)
    schema = str(client.get("/openapi.json").json()).lower()

    for word in ("gemini", "google", "genai"):
        assert word not in schema, f"{word!r} appears in the public OpenAPI schema"
