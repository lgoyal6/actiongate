"""Load the labelled evaluation corpus.

Each corpus file is a list of objects:

    {"payload": <exactly a Circleback webhook body>,
     "labels":  {"<actionItem id>": 0 | 1, ...},
     "hard":    ["<actionItem id>", ...]}     # cases where surface cues mislead

``label = 1`` means: the action item describes a real obligation that somebody in
the meeting actually committed to.  ``label = 0`` means it does not, because it is
hypothetical, conditional on something undecided, hedged into non-commitment,
explicitly retracted, or not supported by anything in the transcript.

The label is about the *extraction being trustworthy*, not about whether the
assignee field happens to be filled in.  That keeps ``assignee_resolved`` an
honest feature rather than a restatement of the label.

The labels never enter the feature extractor: ``load`` hands the classifier only
``payload``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .schema import ActionItem, Meeting

DATA_DIR = Path(__file__).with_name("data")


@dataclass(frozen=True)
class Example:
    meeting: Meeting
    item: ActionItem
    label: int
    hard: bool


def load(split: str) -> list[Example]:
    path = DATA_DIR / f"{split}.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    out: list[Example] = []
    for entry in blob:
        meeting = Meeting.parse(entry["payload"])
        labels = entry["labels"]
        hard = set(str(h) for h in entry.get("hard", []))
        for item in meeting.action_items:
            key = str(item.id)
            if key not in labels:
                raise ValueError(f"{path.name}: action item {key} in {meeting.id} has no label")
            out.append(Example(meeting, item, int(labels[key]), key in hard))
    return out


def summarise(split: str) -> dict:
    ex = load(split)
    return {
        "split": split,
        "meetings": len({e.meeting.id for e in ex}),
        "action_items": len(ex),
        "positives": sum(e.label for e in ex),
        "negatives": sum(1 for e in ex if e.label == 0),
        "hard_cases": sum(1 for e in ex if e.hard),
    }
