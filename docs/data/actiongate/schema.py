"""Circleback webhook payload -> typed objects.

Field names here are copied exactly from Circleback's published schema
(support.circleback.ai article 11014015, read 2026-08-14).  Nothing is invented.
Documented fields:

    id, name, createdAt, duration, url, recordingUrl, tags, icalUid,
    attendees[].{name,email},
    notes,
    actionItems[].{id, title, description, assignee{name,email}|null, status},
    transcript[].{speaker, text, timestamp},
    insights{<insightName>: [{insight, speaker, timestamp}]}

Every field is treated as nullable/absent-tolerant, because a receiver that
throws on a missing optional field is a receiver that drops meetings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Person:
    name: str | None = None
    email: str | None = None

    @classmethod
    def parse(cls, raw: Any) -> "Person | None":
        if not isinstance(raw, dict):
            return None
        return cls(name=_str_or_none(raw.get("name")), email=_str_or_none(raw.get("email")))


@dataclass(frozen=True)
class ActionItem:
    id: int | str
    title: str
    description: str
    assignee: Person | None
    status: str

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "ActionItem":
        return cls(
            id=raw.get("id"),
            title=_str(raw.get("title")),
            description=_str(raw.get("description")),
            assignee=Person.parse(raw.get("assignee")),
            status=_str(raw.get("status")) or "PENDING",
        )


@dataclass(frozen=True)
class Segment:
    speaker: str
    text: str
    timestamp: float

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Segment":
        ts = raw.get("timestamp")
        return cls(
            speaker=_str(raw.get("speaker")),
            text=_str(raw.get("text")),
            timestamp=float(ts) if isinstance(ts, (int, float)) else 0.0,
        )


@dataclass(frozen=True)
class Meeting:
    id: str
    name: str
    created_at: str
    duration: float
    attendees: list[Person] = field(default_factory=list)
    notes: str = ""
    action_items: list[ActionItem] = field(default_factory=list)
    transcript: list[Segment] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    insights: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "Meeting":
        if not isinstance(payload, dict):
            raise ValueError("webhook payload must be a JSON object")
        return cls(
            id=_str(payload.get("id")),
            name=_str(payload.get("name")),
            created_at=_str(payload.get("createdAt")),
            duration=float(payload.get("duration") or 0.0),
            attendees=[p for p in (Person.parse(a) for a in _list(payload.get("attendees"))) if p],
            notes=_str(payload.get("notes")),
            action_items=[
                ActionItem.parse(a) for a in _list(payload.get("actionItems")) if isinstance(a, dict)
            ],
            transcript=[
                Segment.parse(s) for s in _list(payload.get("transcript")) if isinstance(s, dict)
            ],
            tags=[_str(t) for t in _list(payload.get("tags"))],
            insights=payload.get("insights") if isinstance(payload.get("insights"), dict) else {},
        )

    def attendee_emails(self) -> set[str]:
        return {p.email.lower() for p in self.attendees if p.email}

    def attendee_names(self) -> set[str]:
        return {p.name.lower() for p in self.attendees if p.name}


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _str_or_none(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []
