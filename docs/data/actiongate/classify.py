"""Scorers: action item -> confidence in [0, 1].

Two implementations behind one interface.

``RuleScorer``   real, deterministic, offline.  A 9-feature logistic model whose
                 weights are fit by ``fit.py`` on the dev split only.  This is
                 what produces every number in RESULTS.md.

``LlmScorer``    the pluggable slot for an LLM judge.  It ships with a
                 deterministic stub so the pipeline runs end to end with no
                 network, no key and no model download.  Every row it produces
                 is flagged ``synthetic=True`` and must be reported as SYNTHETIC.
                 To make it real, pass a ``complete_fn`` that calls your model;
                 that is the one documented thing you change.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .features import FEATURE_NAMES, Evidence, extract
from .schema import ActionItem, Meeting

WEIGHTS_PATH = Path(__file__).with_name("weights.json")


@dataclass(frozen=True)
class Score:
    meeting_id: str
    action_item_id: object
    confidence: float
    scorer_id: str
    evidence: Evidence
    synthetic: bool = False
    notes: list[str] = field(default_factory=list)


class Scorer(Protocol):
    scorer_id: str
    synthetic: bool

    def score(self, item: ActionItem, meeting: Meeting) -> Score: ...


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass
class RuleScorer:
    """Logistic model over the 9 grounding/language features."""

    weights: dict[str, float]
    bias: float
    scorer_id: str = "rules-logreg-v1"
    synthetic: bool = False

    @classmethod
    def load(cls, path: Path = WEIGHTS_PATH) -> "RuleScorer":
        blob = json.loads(path.read_text())
        return cls(
            weights=blob["weights"],
            bias=blob["bias"],
            scorer_id=blob.get("scorer_id", "rules-logreg-v1"),
        )

    def confidence_from(self, ev: Evidence) -> float:
        z = self.bias + sum(self.weights[n] * getattr(ev, n) for n in FEATURE_NAMES)
        return sigmoid(z)

    def score(self, item: ActionItem, meeting: Meeting) -> Score:
        ev = extract(item, meeting)
        conf = self.confidence_from(ev)
        notes = []
        if ev.grounding < 0.34:
            notes.append("weak transcript grounding: possible extraction error")
        if ev.retraction:
            notes.append("supporting span contains a retraction")
        if ev.hedge_language:
            notes.append("supporting span is hedged")
        if ev.conditional:
            notes.append("commitment is conditional")
        if not ev.assignee_resolved:
            notes.append("assignee does not resolve to a meeting attendee")
        return Score(
            meeting_id=meeting.id,
            action_item_id=item.id,
            confidence=round(conf, 4),
            scorer_id=self.scorer_id,
            evidence=ev,
            synthetic=False,
            notes=notes,
        )


PROMPT = """You are grading one action item extracted from a meeting transcript.
Answer with a single number between 0 and 1: the probability that this is a real
commitment somebody actually made, as opposed to a hypothetical, a hedge, a
retracted item, or an extraction unsupported by the transcript.

ACTION ITEM: {title}
DESCRIPTION: {description}
ASSIGNEE: {assignee}
SUPPORTING TRANSCRIPT SPAN: {span}
"""


def _deterministic_stub(prompt: str) -> str:
    """Offline stand-in for a model call. Deterministic, no network, no weights.

    Returns a number derived only from the prompt text so runs are reproducible.
    It is not a model and its output is not evidence of anything.
    """
    h = sum(ord(c) for c in prompt) % 1000
    return f"{h / 1000:.3f}"


@dataclass
class LlmScorer:
    """Pluggable LLM judge. Stubbed by default; outputs are SYNTHETIC."""

    complete_fn: Callable[[str], str] = _deterministic_stub
    scorer_id: str = "llm-stub-v1"
    synthetic: bool = True

    def __post_init__(self) -> None:
        if self.complete_fn is not _deterministic_stub:
            self.scorer_id = "llm-live"
            self.synthetic = False
        elif os.environ.get("ACTIONGATE_ALLOW_STUB", "1") != "1":
            raise RuntimeError("LLM stub disabled; inject a real complete_fn")

    def score(self, item: ActionItem, meeting: Meeting) -> Score:
        ev = extract(item, meeting)
        prompt = PROMPT.format(
            title=item.title,
            description=item.description,
            assignee=(item.assignee.name if item.assignee else "unassigned"),
            span=ev.grounding_span or "(no supporting span found)",
        )
        try:
            conf = max(0.0, min(1.0, float(self.complete_fn(prompt).strip())))
        except (TypeError, ValueError):
            conf = 0.0
        note = "SYNTHETIC: deterministic stub, not a model" if self.synthetic else "live model"
        return Score(
            meeting_id=meeting.id,
            action_item_id=item.id,
            confidence=round(conf, 4),
            scorer_id=self.scorer_id,
            evidence=ev,
            synthetic=self.synthetic,
            notes=[note],
        )
