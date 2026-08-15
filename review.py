"""The human-review queue, derived from the audit log.

There is deliberately no second mutable store.  The queue is a projection of the
append-only log: an item is pending review if it has a GATE_DECISION of REVIEW
and no later REVIEW_DECISION record.  Approving an item appends a new record; it
never edits the gate's record.  So the log answers "what did the machine think,
what did the human decide, and when" without any reconciliation step.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import AuditLog


@dataclass
class QueueItem:
    key: str
    meeting_id: str
    meeting_name: str
    action_item_id: Any
    title: str
    description: str
    assignee: str | None
    confidence: float
    threshold: float
    reason: str
    evidence: dict
    decision_seq: int
    reviewed: dict | None = None


def _key(meeting_id: str, item_id: Any) -> str:
    return f"{meeting_id}:{item_id}"


def project(log: AuditLog) -> dict[str, QueueItem]:
    """Replay the log into the current review state."""
    items: dict[str, QueueItem] = {}
    for entry in log.entries():
        rec = entry["record"]
        if rec["event"] == "GATE_DECISION" and rec["decision"] == "REVIEW":
            k = _key(rec["meeting_id"], rec["action_item_id"])
            items[k] = QueueItem(
                key=k,
                meeting_id=rec["meeting_id"],
                meeting_name=rec.get("meeting_name", ""),
                action_item_id=rec["action_item_id"],
                title=rec.get("title", ""),
                description=rec.get("description", ""),
                assignee=rec.get("assignee"),
                confidence=rec["confidence"],
                threshold=rec["threshold"],
                reason=rec.get("reason", ""),
                evidence=rec.get("evidence", {}),
                decision_seq=rec["seq"],
            )
        elif rec["event"] == "REVIEW_DECISION":
            k = _key(rec["meeting_id"], rec["action_item_id"])
            if k in items:
                items[k].reviewed = {
                    "verdict": rec["verdict"],
                    "reviewer": rec["reviewer"],
                    "ts": rec["ts"],
                    "seq": rec["seq"],
                    "note": rec.get("note", ""),
                }
    return items


def pending(log: AuditLog) -> list[QueueItem]:
    return sorted(
        (i for i in project(log).values() if i.reviewed is None),
        key=lambda i: i.confidence,
        reverse=True,
    )


def reviewed(log: AuditLog) -> list[QueueItem]:
    return [i for i in project(log).values() if i.reviewed is not None]


def record_review(
    log: AuditLog,
    crm,
    key: str,
    verdict: str,
    reviewer: str,
    note: str = "",
) -> dict:
    """Append a human decision. APPROVE also performs the CRM write."""
    if verdict not in {"APPROVE", "REJECT"}:
        raise ValueError("verdict must be APPROVE or REJECT")
    if not reviewer:
        raise ValueError("reviewer is required: an audit trail with no actor is not one")

    q = project(log).get(key)
    if q is None:
        raise KeyError(f"{key} is not in the review queue")
    if q.reviewed is not None:
        raise ValueError(f"{key} was already reviewed by {q.reviewed['reviewer']}")

    entry = log.append(
        "REVIEW_DECISION",
        meeting_id=q.meeting_id,
        action_item_id=q.action_item_id,
        verdict=verdict,
        reviewer=reviewer,
        note=note,
        decision_seq=q.decision_seq,
        confidence=q.confidence,
    )
    if verdict == "APPROVE":
        crm_id = crm.write_task(
            _MeetingRef(q.meeting_id),
            _ItemRef(q.action_item_id, q.title, q.description, q.assignee),
            q.confidence,
            provenance=f"human:{reviewer};audit_seq={entry['seq']}",
        )
        log.append(
            "CRM_WRITE",
            meeting_id=q.meeting_id,
            action_item_id=q.action_item_id,
            crm_id=crm_id,
            decision_seq=entry["seq"],
            authorised_by=f"human:{reviewer}",
        )
    return entry


@dataclass
class _MeetingRef:
    id: str


@dataclass
class _ItemRef:
    id: Any
    title: str
    description: str
    _email: str | None

    @property
    def assignee(self):
        return _Assignee(self._email) if self._email else None


@dataclass
class _Assignee:
    email: str | None


# ---------------------------------------------------------------- HTML view


def render_html(log: AuditLog, out_path: Path, threshold: float) -> Path:
    q_pending, q_done = pending(log), reviewed(log)
    ok, chain_msg = log.verify_chain()

    def bar(v: float, danger: bool = False) -> str:
        pct = max(0.0, min(1.0, v)) * 100
        colour = "#b3261e" if danger else "#1f6f43"
        return (
            f'<div class="bar"><span style="width:{pct:.1f}%;background:{colour}"></span></div>'
        )

    rows = []
    for i in q_pending:
        ev = i.evidence
        flags = ", ".join(
            n for n in ("hedge_language", "conditional", "retraction", "vague_title")
            if ev.get(n)
        ) or "none"
        rows.append(f"""
      <tr>
        <td class="conf">{i.confidence:.3f}{bar(i.confidence, i.confidence < threshold)}</td>
        <td><strong>{html.escape(i.title)}</strong><div class="desc">{html.escape(i.description)}</div>
            <div class="meta">{html.escape(i.meeting_name)} &middot; assignee: {html.escape(i.assignee or "unassigned")}</div></td>
        <td class="span">{html.escape(str(ev.get("grounding_speaker", "")))}: &ldquo;{html.escape(str(ev.get("grounding_span", "")))}&rdquo;
            <div class="meta">grounding {float(ev.get("grounding", 0)):.2f} &middot; negative flags: {flags}</div></td>
        <td class="cmd"><code>approve {html.escape(i.key)}</code><br><code>reject {html.escape(i.key)}</code></td>
      </tr>""")

    done_rows = "".join(
        f"<tr><td>{i.confidence:.3f}</td><td>{html.escape(i.title)}</td>"
        f"<td>{html.escape(i.reviewed['verdict'])}</td><td>{html.escape(i.reviewed['reviewer'])}</td>"
        f"<td class='meta'>{html.escape(i.reviewed['ts'])}</td></tr>"
        for i in sorted(q_done, key=lambda x: x.reviewed["seq"])
    ) or "<tr><td colspan='5' class='meta'>nothing reviewed yet</td></tr>"

    doc = f"""<!doctype html>
<meta charset="utf-8"><title>ActionGate review queue</title>
<style>
 body{{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:32px;
      background:#fbfaf8;color:#1a1a1a;max-width:1100px}}
 h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:32px 0 8px}}
 .sub{{color:#6b6b6b;margin-bottom:20px}}
 table{{border-collapse:collapse;width:100%}}
 th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#6b6b6b;
     border-bottom:1px solid #e3e0da;padding:6px 10px}}
 td{{border-bottom:1px solid #eeece7;padding:10px;vertical-align:top}}
 .conf{{width:96px;font-variant-numeric:tabular-nums;font-weight:600}}
 .bar{{height:4px;background:#e3e0da;border-radius:2px;margin-top:5px}}
 .bar span{{display:block;height:100%;border-radius:2px}}
 .desc{{color:#4a4a4a;margin-top:3px}}
 .meta{{color:#8a8a8a;font-size:12px;margin-top:4px}}
 .span{{color:#3d3d3d;max-width:380px}}
 code{{background:#f0eee9;padding:2px 5px;border-radius:3px;font-size:12px;white-space:nowrap}}
 .chain{{padding:8px 12px;border-radius:4px;font-size:12px;display:inline-block;
         background:{"#e7f2ea" if ok else "#fbe9e7"};color:{"#1f6f43" if ok else "#b3261e"}}}
</style>
<h1>ActionGate review queue</h1>
<div class="sub">Action items below the auto-commit threshold of
 <strong>{threshold:.2f}</strong>. Nothing here has been written to the CRM.
 Approve or reject with:
 <code>python -m actiongate.cli approve &lt;key&gt; --reviewer you@example.com</code></div>
<div class="chain">audit chain: {html.escape(chain_msg)}</div>
<h2>Pending &middot; {len(q_pending)}</h2>
<table><tr><th>confidence</th><th>action item</th><th>supporting transcript span</th><th>act</th></tr>
{"".join(rows) or "<tr><td colspan='4' class='meta'>queue is empty</td></tr>"}
</table>
<h2>Already reviewed &middot; {len(q_done)}</h2>
<table><tr><th>confidence</th><th>action item</th><th>verdict</th><th>reviewer</th><th>when</th></tr>
{done_rows}</table>
"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    return out_path
