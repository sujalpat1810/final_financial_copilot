"""
The entity gate is the abstention path that scores cannot produce.

Measured on data/calibration.json, four of the six unindexed-company questions
score ABOVE the -6.0 abstention floor — a peer's financial question retrieves
genuinely similar passages from whichever company is indexed.  These tests pin
both halves of the contract: the gate fires on companies the corpus lacks, and
it stays silent on every question the corpus can answer.

The false-positive tests matter more than the true-positive ones.  A gate that
over-fires refuses work the tool exists to do, and it would do so invisibly —
the response looks like a legitimate abstention.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.confidence import Confidence, assess
from app.config import cfg
from app.entities import (
    canonical_entity,
    describe,
    display_name,
    find_mentions,
    foreign_entities,
)

INDEXED = {"Infosys", "TCS"}


@pytest.fixture(autouse=True)
def pinned_thresholds(monkeypatch):
    monkeypatch.setattr(cfg, "rerank_high_threshold", 2.0)
    monkeypatch.setattr(cfg, "rerank_moderate_threshold", -2.0)
    monkeypatch.setattr(cfg, "rerank_abstain_threshold", -6.0)
    monkeypatch.setattr(cfg, "confidence_min_supporting", 2)


# ── Fires on companies the corpus does not cover ──────────────────────────────

@pytest.mark.parametrize("question,expected", [
    ("What was Wipro's revenue in FY2025?", "wipro"),
    ("What was HDFC Bank's net interest margin?", "hdfc bank"),
    ("How many employees does Reliance Industries have?", "reliance industries"),
    ("What is the capital adequacy ratio of State Bank of India?",
     "state bank of india"),
    ("How did Accenture's bookings compare?", "accenture"),
    ("What is Cognizant's headcount?", "cognizant"),
])
def test_unindexed_company_is_detected(question, expected):
    assert foreign_entities(question, INDEXED) == [expected]


def test_unindexed_company_abstains_regardless_of_score():
    """
    The whole point: a top score of 9.0 would be HIGH confidence on its own.
    Naming an unindexed company overrides it, because the score measures topical
    similarity and a peer's financials are topically identical.
    """
    a = assess([9.0, 8.5, 8.0], foreign_entities=["wipro"],
               indexed_entities=["Infosys", "TCS"])
    assert a.level is Confidence.INSUFFICIENT
    assert a.abstained


def test_abstention_reason_names_the_company_and_the_scope():
    a = assess([1.33], foreign_entities=["wipro"],
               indexed_entities=["Infosys", "TCS"])
    assert "Wipro" in a.abstention_reason
    # The reader needs to know what IS covered, or the refusal is a dead end.
    assert "Infosys" in a.abstention_reason and "TCS" in a.abstention_reason


def test_gate_precedes_the_score_floor_in_the_reason():
    """A gated question must not be explained as a weak-evidence abstention."""
    a = assess([7.5], foreign_entities=["wipro"], indexed_entities=["Infosys"])
    assert "not in the index" in a.reason
    assert "below the abstention floor" not in a.reason


# ── Stays silent on everything the corpus can answer ──────────────────────────

@pytest.mark.parametrize("question", [
    "What was Infosys consolidated revenue in FY2024-25?",
    "What was TCS standalone revenue in FY2024-25?",
    "Who audited Infosys and was the opinion unqualified?",
    "What was the profit for the year?",
    "What are the key risk factors?",
    "What is the dividend per share?",
    "What was revenue?",
    "List related party transactions",
    "What contingent liabilities are disclosed?",
])
def test_answerable_questions_are_not_gated(question):
    assert foreign_entities(question, INDEXED) == []


@pytest.mark.parametrize("question", [
    "What does Ind AS 116 require?",
    "What was reported for the year ended March 31, 2025?",
    "What did the Board of Directors recommend?",
    "How much revenue came from India?",
    "What is the Company's policy on hedging?",
    "Summarise the Management Discussion and Analysis.",
])
def test_capitalised_non_companies_do_not_trip_the_gate(question):
    """
    The reason this gate uses a gazetteer and not capitalisation: every one of
    these has a capitalised run that a naive proper-noun rule would call a
    company, and gating any of them would refuse a legitimate question.
    """
    assert foreign_entities(question, INDEXED) == []


def test_no_foreign_entity_leaves_scoring_untouched():
    assert assess([6.4, 3.1, 2.2], foreign_entities=[]).level is Confidence.HIGH
    assert assess([6.4, 3.1, 2.2], foreign_entities=None).level is Confidence.HIGH


# ── Alias handling ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "What was Tata Consultancy Services revenue?",
    "What was TCS revenue?",
    "What was Infosys Limited's revenue?",
    "What was Infosys Ltd revenue?",
])
def test_aliases_of_indexed_entities_are_not_foreign(question):
    """
    'Tata Consultancy Services' must resolve to the indexed 'TCS'.  Longest-match
    consumption is what stops the 'Tata …' prefix being read as a second,
    unindexed company in the same question.
    """
    assert foreign_entities(question, INDEXED) == []


def test_canonical_entity_resolves_aliases():
    assert canonical_entity("Tata Consultancy Services") == "TCS"
    assert canonical_entity("infosys limited") == "Infosys"
    assert canonical_entity("Wipro") is None


def test_matching_is_case_insensitive():
    assert foreign_entities("what was WIPRO revenue", INDEXED) == ["wipro"]
    assert foreign_entities("what was wipro revenue", INDEXED) == ["wipro"]


def test_indexed_set_is_honoured_not_hardcoded():
    """Ingesting Wipro must stop the gate firing on Wipro, with no code change."""
    assert foreign_entities("What was Wipro's revenue?", {"Infosys", "Wipro"}) == []
    # And an entity dropping out of the index brings the gate back.
    assert foreign_entities("What was TCS revenue?", {"Infosys"}) == ["tcs"]


def test_indexed_entity_comparison_is_case_insensitive():
    """--entity is typed by hand; 'infosys' is not a different company."""
    assert foreign_entities("What was Infosys revenue?", {"infosys"}) == []


# ── Suffix rule for the long tail ─────────────────────────────────────────────

def test_corporate_suffix_catches_companies_not_in_the_gazetteer():
    assert foreign_entities("What was Zomato Limited's revenue?", INDEXED) \
        == ["zomato limited"]


def test_suffix_rule_does_not_fire_without_a_suffix():
    """A bare capitalised token is not enough — that is the false-positive trap."""
    assert foreign_entities("What was Zomato revenue?", INDEXED) == []


# ── Mixed questions ───────────────────────────────────────────────────────────

def test_question_naming_both_an_indexed_and_an_unindexed_company_is_gated():
    """
    Deliberate: a comparison the corpus can only half-answer is refused rather
    than answered for the half it has.  Answering "compare Infosys and Wipro"
    with Infosys figures alone invites the reader to attribute them to both.
    """
    assert foreign_entities(
        "Compare Infosys and Wipro revenue for FY2025", INDEXED
    ) == ["wipro"]


def test_multiple_unindexed_companies_are_all_reported():
    found = foreign_entities("Compare Wipro and Accenture margins", INDEXED)
    assert set(found) == {"wipro", "accenture"}


def test_describe_reads_as_prose():
    assert describe(["wipro"]) == "Wipro"
    assert describe(["wipro", "accenture"]) == "Wipro and Accenture"
    assert describe(["hdfc bank", "wipro", "accenture"]) \
        == "HDFC Bank, Wipro and Accenture"


@pytest.mark.parametrize("key,shown", [
    ("hdfc bank", "HDFC Bank"),
    ("state bank of india", "State Bank of India"),
    ("tcs", "TCS"),
    ("ibm", "IBM"),
    ("l&t", "L&T"),
    ("ltimindtree", "LTIMindtree"),
    ("dr reddy's", "Dr Reddy's"),
    # Long tail from the suffix rule has no curated spelling; title-case is right.
    ("zomato limited", "Zomato Limited"),
])
def test_display_names_are_not_mangled_by_title_case(key, shown):
    """
    These strings are shown to accountants inside a refusal.  ".title()" alone
    yields "Hdfc Bank" and "State Bank Of India", which reads as though the tool
    does not recognise the company it is declining to discuss.
    """
    assert display_name(key) == shown


def test_find_mentions_does_not_double_report_overlapping_names():
    mentions = find_mentions("Tata Consultancy Services results")
    assert mentions == ["tata consultancy services"]


# ── The calibration set, end to end ───────────────────────────────────────────

def test_every_absent_calibration_question_now_abstains():
    """
    Regression guard on the gap this gate was built to close: before it, only 2
    of these 6 abstained.  The gate covers the company questions; the score floor
    covers the two that are not about companies at all.
    """
    path = Path(__file__).parent.parent / "data" / "calibration.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    absent = [e for e in entries if e["kind"] == "absent"]
    assert absent, "calibration fixture has no absent questions"

    for e in absent:
        foreign = foreign_entities(e["question"], INDEXED)
        a = assess(e["scores"], foreign_entities=foreign,
                   indexed_entities=sorted(INDEXED))
        assert a.abstained, f"failed to abstain: {e['question']} (top {e['top']})"


def test_no_answerable_calibration_question_is_gated():
    path = Path(__file__).parent.parent / "data" / "calibration.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    for e in entries:
        if e["kind"] == "absent":
            continue
        assert foreign_entities(e["question"], INDEXED) == [], \
            f"false positive on {e['kind']} question: {e['question']}"
