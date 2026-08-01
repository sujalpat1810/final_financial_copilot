"""
Mapping reranker scores to a confidence label, and deciding when to abstain.

Why surface confidence at all
─────────────────────────────
Without it, an answer built from a strong direct quote and an answer scraped
from a weak semantic match arrive with identical authority.  For chartered
accountants and lawyers — who carry liability for what they rely on — that is
the single most misleading thing the UI could do.  A system that visibly knows
the limits of its evidence is more persuasive than one that always answers.

Why raw logits and not probabilities
────────────────────────────────────
cross-encoder/ms-marco-MiniLM-L-6-v2 emits unbounded scores, roughly -11..+11.
Passing them through a sigmoid squashes irrelevant passages to ~0.00002 and good
ones to ~0.9997, so every decision boundary would fall inside a rounding error
and the numbers shown to the user would be 0.00 or 1.00 with nothing between.
Thresholds are therefore compared against the raw logit.

Display relevance is a SEPARATE concern.  `relevance()` maps the logit onto
0-100 by clamping and scaling — monotone, readable, and it spreads the range
where sigmoid collapses it.  It is a presentation transform only; nothing in the
ranking or the thresholds uses it.

Thresholds live in app/config.py and are env-overridable.  The shipped defaults
are a starting point, not calibrated values — see the note there.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.config import cfg

# Logits outside this band carry no extra information for display purposes — the
# model is already saturated — so the 0-100 scale clamps to it.
_DISPLAY_FLOOR = -10.0
_DISPLAY_CEILING = 10.0


class Confidence(str, Enum):
    """
    str-valued so it serialises as "high"/"moderate"/… straight into JSON.

    INSUFFICIENT is not a weaker LOW: it means no answer was generated at all.
    """

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class Assessment:
    level: Confidence
    reason: str
    abstention_reason: str | None = None

    @property
    def abstained(self) -> bool:
        """Derived, never stored — the two must not be able to disagree."""
        return self.level is Confidence.INSUFFICIENT


def relevance(score: float | None) -> int:
    """
    Map a reranker logit to a 0-100 display value.

    Presentation only. Monotone in the logit, so ordering is preserved, but the
    number is not a probability and must not be described as one.
    """
    if score is None:
        return 0
    clamped = max(_DISPLAY_FLOOR, min(_DISPLAY_CEILING, float(score)))
    span = _DISPLAY_CEILING - _DISPLAY_FLOOR
    return round((clamped - _DISPLAY_FLOOR) / span * 100)


def assess(scores: list[float | None]) -> Assessment:
    """
    Decide the confidence level from reranker scores, highest first or not.

    Takes bare scores rather than RetrievedChunk objects so the decision
    boundaries are testable without constructing a retrieval pipeline.
    """
    usable = [float(s) for s in scores if s is not None]

    if not usable:
        # Either retrieval returned nothing, or it returned chunks the reranker
        # never scored. Both mean there is no evidence to reason about.
        detail = (
            "no chunks were retrieved"
            if not scores
            else f"none of the {len(scores)} retrieved chunks carried a reranker score"
        )
        return Assessment(
            level=Confidence.INSUFFICIENT,
            reason=detail,
            abstention_reason=f"No usable evidence: {detail}.",
        )

    top = max(usable)
    supporting = sum(1 for s in usable if s >= cfg.rerank_moderate_threshold)

    if top < cfg.rerank_abstain_threshold:
        return Assessment(
            level=Confidence.INSUFFICIENT,
            reason=f"top match {top:.1f}, below the abstention floor "
                   f"{cfg.rerank_abstain_threshold:.1f}",
            abstention_reason=(
                "The indexed documents don't contain enough information to answer "
                "this reliably. The closest matches all scored below the "
                "confidence threshold."
            ),
        )

    if top >= cfg.rerank_high_threshold:
        if supporting >= cfg.confidence_min_supporting:
            return Assessment(
                level=Confidence.HIGH,
                reason=f"top match {top:.1f}, {supporting} supporting chunks",
            )
        # A single strong chunk is one point of failure: nothing corroborates it,
        # so it does not earn "high" however good the score looks.
        return Assessment(
            level=Confidence.MODERATE,
            reason=f"top match {top:.1f}, but only {supporting} supporting chunk",
        )

    if top >= cfg.rerank_moderate_threshold:
        return Assessment(
            level=Confidence.MODERATE,
            reason=f"top match {top:.1f}, below the high threshold "
                   f"{cfg.rerank_high_threshold:.1f}",
        )

    return Assessment(
        level=Confidence.LOW,
        reason=f"top match {top:.1f}, weak evidence — treat with caution",
    )
