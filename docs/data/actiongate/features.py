"""Features for deciding whether an extracted action item is a real commitment.

Everything here is computed from fields Circleback actually sends: the action
item itself, the attendee list, and the transcript segments.  No external calls.

The central idea is *grounding*: an action item is only as trustworthy as the
transcript span that supports it.  We locate the best-matching span, then read
the language in that span (commitment, hedging, conditionals, retractions) and
who was speaking.  An action item with no supporting span is the dangerous case,
because that is what an extraction error looks like from the outside.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .schema import ActionItem, Meeting, Segment

FEATURE_NAMES = [
    "grounding",
    "commit_language",
    "hedge_language",
    "conditional",
    "retraction",
    "assignee_resolved",
    "temporal_anchor",
    "speaker_is_assignee",
    "vague_title",
]

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "he", "her", "him", "his", "how", "i", "in", "is", "it", "its", "of", "on",
    "or", "our", "she", "so", "that", "the", "their", "them", "then", "there",
    "they", "this", "to", "was", "we", "were", "what", "when", "which", "who",
    "will", "with", "you", "your", "'ll", "im", "ive", "do", "does", "did", "if",
    "us", "me", "my", "not", "but", "can", "could", "would", "should", "get",
    "got", "go", "going", "let", "s", "t", "ll", "re", "ve", "m", "d", "up", "out",
    "about", "over", "into", "than", "all", "any", "some", "just", "also",
}

_WORD = re.compile(r"[a-z0-9']+")

# First-person / accepted-ownership commitment.
_COMMIT = re.compile(
    r"\b(i'?ll|i will|we'?ll|we will|i'?m going to|we'?re going to|i am going to"
    r"|let me|i'?ll take|i'?ve got (it|that)|i got (it|that)|on it"
    r"|consider it done|i'?ll own|i'?ll handle|i'?ll get|i'?ll send|i'?ll set up"
    r"|you'?ll have|i'?ll put together|i'?ll write|i'?ll pull|i'?ll circulate"
    r"|sending (it|that|you)|goes out|will be in your inbox|i commit)\b"
)

# Language that marks an idea rather than a promise.
_HEDGE = re.compile(
    r"\b(maybe|might|perhaps|possibly|potentially|probably|at some point|someday"
    r"|not sure|no promises|tentatively|in theory|ideally|would be nice"
    r"|it'?d be nice|nice to have|we could|could probably|someone should"
    r"|somebody should|we should think|thinking about|kicking around|toying with"
    r"|open to|worth exploring|worth considering|down the road|eventually"
    r"|no commitment|hypothetically|brainstorm)\b"
)

_CONDITIONAL = re.compile(
    r"\b(if we|if you|if that|if they|if the|assuming|depending on|once we"
    r"|once you|provided that|in the event|should we decide|pending|contingent"
    r"|subject to|only if|in case)\b"
)

_RETRACTION = re.compile(
    r"\b(scratch that|never ?mind|hold off|let'?s not|disregard|on second thought"
    r"|cancel that|forget (that|it)|strike that|actually,? no|pull(ing)? that back"
    r"|we'?re not doing|drop(ping)? that|walk that back)\b"
)

_TEMPORAL = re.compile(
    r"\b(today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday"
    r"|sunday|eod|cob|end of (the )?(day|week|month)|this (week|afternoon|morning)"
    r"|next (week|month|monday|tuesday|wednesday|thursday|friday)|by (the )?\d"
    r"|in (the )?(morning|afternoon)|q[1-4]|\d{1,2}(st|nd|rd|th)"
    r"|jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|aug(ust)?"
    r"|sep(t|tember)?|oct(ober)?|nov(ember)?|dec(ember)?|within \d+|\d+ ?(hours|days|weeks))\b"
)

# Titles that are pure ceremony with no object.
_VAGUE_TITLES = {
    "follow up", "circle back", "sync", "touch base", "connect", "chat",
    "discuss", "check in", "keep in touch", "revisit", "look into it",
    "follow up later", "reconnect", "align", "regroup",
}


@dataclass(frozen=True)
class Evidence:
    """Why the classifier scored an item the way it did. Goes into the audit log."""

    grounding: float
    commit_language: float
    hedge_language: float
    conditional: float
    retraction: float
    assignee_resolved: float
    temporal_anchor: float
    speaker_is_assignee: float
    vague_title: float
    grounding_span: str = ""
    grounding_speaker: str = ""
    grounding_timestamp: float = -1.0

    def vector(self) -> list[float]:
        return [getattr(self, n) for n in FEATURE_NAMES]

    def as_dict(self) -> dict:
        return asdict(self)


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _window_text(transcript: list[Segment], i: int, size: int) -> str:
    return " ".join(s.text for s in transcript[i : i + size])


# How far past the supporting span to look for a retraction. A commitment is
# walked back *after* it is made ("I'll do X." ... "actually, hold off"), so a
# retraction is invisible to any window that stops at the commitment sentence.
LOOKAHEAD_SEGMENTS = 3


def find_grounding(item: ActionItem, meeting: Meeting) -> tuple[float, int, int]:
    """Best transcript window supporting this action item.

    Returns (coverage in [0,1], start index, window size).  Coverage is the
    fraction of the action item's content words that appear in the window,
    maximised over sliding windows of 1..3 consecutive segments.
    """
    target = _tokens(f"{item.title} {item.description}")
    if not target or not meeting.transcript:
        return 0.0, -1, 0

    best_cov, best_i, best_size = 0.0, -1, 0
    for size in (1, 2, 3):
        for i in range(len(meeting.transcript) - size + 1):
            cov = len(target & _tokens(_window_text(meeting.transcript, i, size))) / len(target)
            if cov > best_cov:
                best_cov, best_i, best_size = cov, i, size
    return best_cov, best_i, best_size


def extract(item: ActionItem, meeting: Meeting) -> Evidence:
    coverage, start, size = find_grounding(item, meeting)
    span = meeting.transcript[start : start + size] if start >= 0 else []
    span_text = " ".join(s.text for s in span).lower()

    # Retraction is checked over the span *and* what was said just after it.
    follow = meeting.transcript[start + size : start + size + LOOKAHEAD_SEGMENTS] if start >= 0 else []
    retraction_text = span_text + " " + " ".join(s.text for s in follow).lower()

    assignee = item.assignee
    assignee_email = (assignee.email or "").lower() if assignee else ""
    assignee_name = (assignee.name or "").lower() if assignee else ""
    resolved = bool(assignee_email and assignee_email in meeting.attendee_emails())

    # Did the assignee themselves speak in the supporting span?
    speakers = {s.speaker.lower() for s in span}
    self_committed = bool(assignee_name and any(assignee_name in sp or sp in assignee_name
                                                for sp in speakers if sp))

    title_norm = re.sub(r"[^a-z ]", "", item.title.lower()).strip()

    return Evidence(
        grounding=round(coverage, 4),
        commit_language=1.0 if _COMMIT.search(span_text) else 0.0,
        hedge_language=1.0 if _HEDGE.search(span_text) else 0.0,
        conditional=1.0 if _CONDITIONAL.search(span_text) else 0.0,
        retraction=1.0 if _RETRACTION.search(retraction_text) else 0.0,
        assignee_resolved=1.0 if resolved else 0.0,
        temporal_anchor=1.0 if _TEMPORAL.search(span_text + " " + item.description.lower()) else 0.0,
        speaker_is_assignee=1.0 if self_committed else 0.0,
        vague_title=1.0 if title_norm in _VAGUE_TITLES else 0.0,
        grounding_span=" ".join(s.text for s in span)[:400],
        grounding_speaker=span[0].speaker if span else "",
        grounding_timestamp=span[0].timestamp if span else -1.0,
    )
