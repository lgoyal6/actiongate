"""Webhook -> verify -> classify -> gate -> (CRM | review queue), all audited.

Order of operations matters and is enforced here:

1. Verify the HMAC over the raw bytes.  An unverified body is never parsed into
   the pipeline and never reaches the classifier.
2. Log acceptance with a hash of the body, so the audit trail can be tied back to
   the exact bytes received without storing meeting content in the log.
3. Score and gate each action item, logging the decision *before* any write.
4. Only then attempt the CRM write.

If the process dies between 3 and 4 the audit log shows a gate decision with no
matching CRM_WRITE, which is a recoverable state you can detect.  The reverse
order would give you CRM rows nobody can explain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .classify import RuleScorer, Scorer
from .gate import Gate
from .schema import Meeting
from .signature import verify


class SignatureError(Exception):
    """Raised when the x-signature header does not verify against the raw body."""


@dataclass
class CrmSink:
    """Stand-in for HubSpot / Salesforce / Attio.

    Swap this one class for a real client and the rest of the pipeline is
    unchanged.  It appends to a JSONL file so the demo needs no credentials.
    """

    path: Path

    def write_task(self, meeting: Meeting, item, confidence: float, provenance: str) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        crm_id = "crm_" + sha256(f"{meeting.id}:{item.id}".encode()).hexdigest()[:12]
        row = {
            "crm_id": crm_id,
            "subject": item.title,
            "body": item.description,
            "owner_email": item.assignee.email if item.assignee else None,
            "source_meeting": f"https://circleback.ai/meetings/{meeting.id}",
            "confidence": confidence,
            "provenance": provenance,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return crm_id


@dataclass
class Pipeline:
    signing_secret: str
    audit: AuditLog
    crm: CrmSink
    gate: Gate
    scorer: Scorer

    def handle(self, raw_body: bytes, signature_header: str | None) -> dict[str, Any]:
        body_sha = sha256(raw_body).hexdigest()

        if not verify(raw_body, signature_header, self.signing_secret):
            self.audit.append(
                "WEBHOOK_REJECTED",
                body_sha256=body_sha,
                bytes_received=len(raw_body),
                reason="x-signature missing, malformed, or mismatched",
            )
            raise SignatureError("invalid x-signature")

        payload = json.loads(raw_body.decode("utf-8"))
        meeting = Meeting.parse(payload)
        self.audit.append(
            "WEBHOOK_ACCEPTED",
            body_sha256=body_sha,
            bytes_received=len(raw_body),
            meeting_id=meeting.id,
            action_items=len(meeting.action_items),
        )

        results = []
        for item in meeting.action_items:
            score = self.scorer.score(item, meeting)
            gr = self.gate.decide(score)
            entry = self.audit.append(
                "GATE_DECISION",
                meeting_id=meeting.id,
                meeting_name=meeting.name,
                action_item_id=item.id,
                title=item.title,
                description=item.description,
                assignee=(item.assignee.email if item.assignee else None),
                confidence=gr.confidence,
                threshold=gr.threshold,
                decision=gr.decision.value,
                reason=gr.reason,
                scorer_id=score.scorer_id,
                policy_id=self.gate.policy_id,
                synthetic_score=score.synthetic,
                evidence=score.evidence.as_dict(),
            )
            crm_id = None
            if gr.commits_now:
                crm_id = self.crm.write_task(
                    meeting, item, gr.confidence, provenance=f"audit_seq={entry['seq']}"
                )
                self.audit.append(
                    "CRM_WRITE",
                    meeting_id=meeting.id,
                    action_item_id=item.id,
                    crm_id=crm_id,
                    decision_seq=entry["seq"],
                    authorised_by="gate",
                )
            results.append(
                {
                    "action_item_id": item.id,
                    "title": item.title,
                    "confidence": gr.confidence,
                    "decision": gr.decision.value,
                    "reason": gr.reason,
                    "crm_id": crm_id,
                    "audit_seq": entry["seq"],
                }
            )

        auto = sum(1 for r in results if r["decision"] == "AUTO_COMMIT")
        return {
            "meeting_id": meeting.id,
            "meeting_name": meeting.name,
            "action_items": len(results),
            "auto_committed": auto,
            "queued_for_review": len(results) - auto,
            "results": results,
        }


def default_pipeline(state_dir: Path, signing_secret: str, threshold: float | None = None) -> Pipeline:
    state_dir = Path(state_dir)
    gate = Gate(auto_commit_at=threshold) if threshold is not None else Gate()
    return Pipeline(
        signing_secret=signing_secret,
        audit=AuditLog(state_dir / "audit.jsonl"),
        crm=CrmSink(state_dir / "crm.jsonl"),
        gate=gate,
        scorer=RuleScorer.load(),
    )
