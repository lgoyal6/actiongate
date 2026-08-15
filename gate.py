"""The confidence gate: decide whether an action item may reach the CRM.

Threshold policy (see RESULTS.md for the precision/recall sweep that chose it):

    confidence >= auto_commit_at   ->  AUTO_COMMIT   written to the CRM
    confidence <  auto_commit_at   ->  REVIEW        queued for a human

The asymmetry is deliberate and it is the whole point.  A false positive is a
wrong action item sitting in a customer's record, attributed to a person who
never agreed to it; somebody finds it weeks later and stops trusting the record.
A false negative costs one row in a review queue.  So the threshold is chosen to
hold precision on the auto-commit band above a stated target, and recall is
whatever that buys, rather than the other way round.

Nothing is silently discarded.  Every item lands in exactly one of the two bands
and every band transition is written to the audit log before the CRM write is
attempted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .classify import Score

# Chosen on the dev split by minimising 20*(wrong CRM writes) + (human reviews),
# then reported on the held-out test split. Stable for any cost ratio from 5 to
# 100. See RESULTS.md for the sweep.
DEFAULT_AUTO_COMMIT_AT = 0.65
PRECISION_TARGET = 0.95


class Decision(str, Enum):
    AUTO_COMMIT = "AUTO_COMMIT"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class GateResult:
    decision: Decision
    confidence: float
    threshold: float
    reason: str
    score: Score

    @property
    def commits_now(self) -> bool:
        return self.decision is Decision.AUTO_COMMIT


@dataclass(frozen=True)
class Gate:
    auto_commit_at: float = DEFAULT_AUTO_COMMIT_AT
    policy_id: str = "gate-v1"

    def decide(self, score: Score) -> GateResult:
        if score.confidence >= self.auto_commit_at:
            return GateResult(
                decision=Decision.AUTO_COMMIT,
                confidence=score.confidence,
                threshold=self.auto_commit_at,
                reason=f"confidence {score.confidence:.3f} >= {self.auto_commit_at:.2f}",
                score=score,
            )
        why = "; ".join(score.notes) or "below auto-commit threshold"
        return GateResult(
            decision=Decision.REVIEW,
            confidence=score.confidence,
            threshold=self.auto_commit_at,
            reason=f"confidence {score.confidence:.3f} < {self.auto_commit_at:.2f} ({why})",
            score=score,
        )
