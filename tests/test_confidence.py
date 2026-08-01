"""
Confidence and abstention are decision boundaries, so they get boundary tests.

Thresholds are env-overridable, so every test pins them explicitly rather than
relying on the shipped defaults — otherwise calibration in Phase 3 would silently
rewrite what these tests assert.
"""

from __future__ import annotations

import pytest

from app.confidence import Assessment, Confidence, assess, relevance
from app.config import Config, cfg

HIGH, MODERATE, ABSTAIN = 2.0, -2.0, -6.0


@pytest.fixture(autouse=True)
def pinned_thresholds(monkeypatch):
    monkeypatch.setattr(cfg, "rerank_high_threshold", HIGH)
    monkeypatch.setattr(cfg, "rerank_moderate_threshold", MODERATE)
    monkeypatch.setattr(cfg, "rerank_abstain_threshold", ABSTAIN)
    monkeypatch.setattr(cfg, "confidence_min_supporting", 2)


# ── Level mapping ─────────────────────────────────────────────────────────────

def test_strong_top_with_agreement_is_high():
    assert assess([6.4, 3.1, 2.2]).level is Confidence.HIGH


def test_strong_top_alone_is_only_moderate():
    """
    One strong chunk is a single point of failure — nothing corroborates it — so
    it does not earn "high" however good the score looks.
    """
    assert assess([8.9, -5.0, -5.5]).level is Confidence.MODERATE


def test_midrange_top_is_moderate():
    assert assess([0.4, -3.0]).level is Confidence.MODERATE


def test_weak_top_is_low_not_abstention():
    """Between the abstention floor and the moderate threshold: answer, but caution."""
    a = assess([-4.5, -7.0])
    assert a.level is Confidence.LOW
    assert not a.abstained


def test_below_floor_abstains():
    a = assess([-7.8, -8.2])
    assert a.level is Confidence.INSUFFICIENT
    assert a.abstained
    assert a.abstention_reason


# ── Exact boundaries. Each threshold is inclusive at its own level. ───────────

@pytest.mark.parametrize("scores,expected", [
    ([HIGH, MODERATE],          Confidence.HIGH),      # both exactly at threshold
    ([HIGH, MODERATE - 0.01],   Confidence.MODERATE),  # only one supporting
    ([HIGH - 0.01, MODERATE],   Confidence.MODERATE),  # top just under high
    ([MODERATE, MODERATE],      Confidence.MODERATE),  # top exactly at moderate
    ([MODERATE - 0.01],         Confidence.LOW),       # just under moderate
    ([ABSTAIN],                 Confidence.LOW),       # floor is inclusive: not abstention
    ([ABSTAIN - 0.01],          Confidence.INSUFFICIENT),
])
def test_threshold_boundaries(scores, expected):
    assert assess(scores).level is expected


def test_min_supporting_is_honoured(monkeypatch):
    monkeypatch.setattr(cfg, "confidence_min_supporting", 3)
    assert assess([6.0, 3.0]).level is Confidence.MODERATE
    assert assess([6.0, 3.0, 2.5]).level is Confidence.HIGH


def test_order_of_scores_does_not_matter():
    """Retrieval returns reranked order, but assess must not depend on it."""
    assert assess([1.0, 6.4, -3.0]).level is assess([6.4, 1.0, -3.0]).level


# ── Degenerate input ──────────────────────────────────────────────────────────

def test_no_results_abstains():
    a = assess([])
    assert a.abstained
    assert "no chunks" in a.reason


def test_unscored_chunks_abstain_rather_than_defaulting_to_zero():
    """
    An unscored chunk is a pipeline failure, not a score of 0.0 — treating it as
    zero would land it in MODERATE and produce a confident-looking answer from
    evidence that was never ranked.
    """
    a = assess([None, None])
    assert a.abstained
    assert "reranker score" in a.reason


def test_partially_scored_uses_only_real_scores():
    assert assess([None, 6.4, None, 2.2]).level is Confidence.HIGH


def test_abstained_is_derived_not_stored():
    """The flag and the level must not be able to disagree."""
    assert Assessment(level=Confidence.INSUFFICIENT, reason="x").abstained
    assert not Assessment(level=Confidence.LOW, reason="x").abstained


def test_non_abstaining_levels_carry_no_abstention_reason():
    for scores in ([6.4, 3.0], [0.4], [-4.5]):
        assert assess(scores).abstention_reason is None


def test_reason_is_populated_for_every_level():
    for scores in ([6.4, 3.0], [8.9], [0.4], [-4.5], [-9.0], []):
        assert assess(scores).reason


# ── Display transform ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("logit,expected", [
    (0.0, 50),
    (10.0, 100),
    (-10.0, 0),
    (6.4, 82),      # the value shown in the design mockup
    (-7.8, 11),     # the abstention example in the mockup
    (99.0, 100),    # clamped
    (-99.0, 0),     # clamped
])
def test_relevance_scale(logit, expected):
    assert relevance(logit) == expected


def test_relevance_of_missing_score():
    assert relevance(None) == 0


def test_relevance_is_monotone():
    values = [relevance(s) for s in (-12, -6, -2, 0, 2, 6, 12)]
    assert values == sorted(values)


def test_relevance_is_not_a_probability():
    """
    Guards the reason this transform exists: a sigmoid would map both of these to
    the same displayed value once rounded, losing the distinction entirely.
    """
    assert relevance(6.0) != relevance(9.0)


# ── Config validation ─────────────────────────────────────────────────────────

def test_inverted_thresholds_are_rejected(monkeypatch):
    monkeypatch.setenv("RERANK_HIGH_THRESHOLD", "-5.0")
    monkeypatch.setenv("RERANK_MODERATE_THRESHOLD", "0.0")
    with pytest.raises(ValueError, match="must satisfy"):
        Config().validate()


def test_equal_thresholds_are_rejected(monkeypatch):
    monkeypatch.setenv("RERANK_HIGH_THRESHOLD", "0.0")
    monkeypatch.setenv("RERANK_MODERATE_THRESHOLD", "0.0")
    with pytest.raises(ValueError, match="must satisfy"):
        Config().validate()


def test_zero_min_supporting_is_rejected(monkeypatch):
    monkeypatch.setenv("CONFIDENCE_MIN_SUPPORTING", "0")
    with pytest.raises(ValueError, match="CONFIDENCE_MIN_SUPPORTING"):
        Config().validate()


def test_shipped_defaults_are_valid():
    Config().validate()
