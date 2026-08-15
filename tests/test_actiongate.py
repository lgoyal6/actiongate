"""Tests. Run: python -m actiongate.tests.test_actiongate  (or pytest)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from actiongate.audit import AuditLog
from actiongate.classify import RuleScorer
from actiongate.corpus import load
from actiongate.gate import Decision, Gate
from actiongate.pipeline import CrmSink, Pipeline, SignatureError
from actiongate.review import pending, record_review, render_html
from actiongate.signature import compute_signature, verify

SECRET = "whsec_test_secret_value"
BODY = b'{"id":"m1","name":"Test","actionItems":[],"transcript":[],"attendees":[]}'


class TestSignature(unittest.TestCase):
    def test_valid_signature_accepted(self):
        self.assertTrue(verify(BODY, compute_signature(BODY, SECRET), SECRET))

    def test_uppercase_hex_accepted(self):
        self.assertTrue(verify(BODY, compute_signature(BODY, SECRET).upper(), SECRET))

    def test_whitespace_tolerated(self):
        self.assertTrue(verify(BODY, "  " + compute_signature(BODY, SECRET) + "\n", SECRET))

    def test_tampered_body_rejected(self):
        sig = compute_signature(BODY, SECRET)
        self.assertFalse(verify(BODY.replace(b"Test", b"Evil"), sig, SECRET))

    def test_wrong_secret_rejected(self):
        self.assertFalse(verify(BODY, compute_signature(BODY, "whsec_other"), SECRET))

    def test_missing_header_rejected(self):
        self.assertFalse(verify(BODY, None, SECRET))

    def test_malformed_headers_rejected(self):
        for bad in ["", "not-hex", "abc", "z" * 64, compute_signature(BODY, SECRET)[:-1]]:
            self.assertFalse(verify(BODY, bad, SECRET), bad)

    def test_empty_secret_refused(self):
        with self.assertRaises(ValueError):
            verify(BODY, compute_signature(BODY, SECRET), "")

    def test_str_body_refused(self):
        """Passing a re-serialized string instead of raw bytes must not silently work."""
        with self.assertRaises(TypeError):
            compute_signature(BODY.decode(), SECRET)  # type: ignore[arg-type]

    def test_byte_exact_not_json_equivalent(self):
        """The bug in the published sample verifier, asserted as a test.

        These two bodies are the same JSON document but different bytes, so they
        have different signatures. Verifying a re-serialized copy therefore
        rejects legitimate requests.
        """
        a = b'{"duration":800.00,"id":"m1"}'
        b = json.dumps(json.loads(a), separators=(",", ":"), sort_keys=True).encode()
        self.assertNotEqual(a, b)
        self.assertEqual(json.loads(a), json.loads(b))
        self.assertFalse(verify(b, compute_signature(a, SECRET), SECRET))


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = AuditLog(self.dir / "audit.jsonl")

    def test_chain_verifies(self):
        for i in range(5):
            self.log.append("TEST_EVENT", i=i)
        ok, msg = self.log.verify_chain()
        self.assertTrue(ok, msg)
        self.assertIn("5 records", msg)

    def test_empty_chain_verifies(self):
        self.assertTrue(self.log.verify_chain()[0])

    def test_tampered_record_detected(self):
        for i in range(4):
            self.log.append("TEST_EVENT", i=i)
        lines = self.log.path.read_text().splitlines()
        entry = json.loads(lines[1])
        entry["record"]["i"] = 999  # edit history, keep the hash
        lines[1] = json.dumps(entry, sort_keys=True)
        self.log.path.write_text("\n".join(lines) + "\n")
        ok, msg = self.log.verify_chain()
        self.assertFalse(ok)
        self.assertIn("altered at seq 1", msg)

    def test_deleted_record_detected(self):
        for i in range(4):
            self.log.append("TEST_EVENT", i=i)
        lines = self.log.path.read_text().splitlines()
        del lines[2]
        self.log.path.write_text("\n".join(lines) + "\n")
        self.assertFalse(self.log.verify_chain()[0])


class TestPipelineAndReview(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = AuditLog(self.dir / "audit.jsonl")
        self.crm = CrmSink(self.dir / "crm.jsonl")
        self.pipe = Pipeline(SECRET, self.log, self.crm, Gate(0.65), RuleScorer.load())
        payload = json.loads((Path(__file__).parents[1] / "data" / "dev.json").read_text())[0]
        self.raw = json.dumps(payload["payload"]).encode()

    def test_bad_signature_writes_nothing(self):
        with self.assertRaises(SignatureError):
            self.pipe.handle(self.raw, "0" * 64)
        self.assertFalse(self.crm.path.exists())
        events = [e["record"]["event"] for e in self.log.entries()]
        self.assertEqual(events, ["WEBHOOK_REJECTED"])

    def test_good_signature_gates_items(self):
        summary = self.pipe.handle(self.raw, compute_signature(self.raw, SECRET))
        self.assertEqual(summary["action_items"], 5)
        self.assertEqual(summary["auto_committed"] + summary["queued_for_review"], 5)
        self.assertTrue(self.log.verify_chain()[0])
        # every auto-commit has a CRM_WRITE authorised by the gate
        writes = [e["record"] for e in self.log.entries() if e["record"]["event"] == "CRM_WRITE"]
        self.assertEqual(len(writes), summary["auto_committed"])
        self.assertTrue(all(w["authorised_by"] == "gate" for w in writes))

    def test_review_appends_and_writes(self):
        self.pipe.handle(self.raw, compute_signature(self.raw, SECRET))
        queued = pending(self.log)
        self.assertTrue(queued)
        before = len(list(self.log.entries()))
        record_review(self.log, self.crm, queued[0].key, "APPROVE", "laksh@example.com", "checked")
        after = list(self.log.entries())
        self.assertEqual(len(after), before + 2)  # REVIEW_DECISION + CRM_WRITE
        self.assertEqual(after[-1]["record"]["authorised_by"], "human:laksh@example.com")
        self.assertTrue(self.log.verify_chain()[0])
        self.assertNotIn(queued[0].key, [q.key for q in pending(self.log)])

    def test_reject_does_not_write_to_crm(self):
        self.pipe.handle(self.raw, compute_signature(self.raw, SECRET))
        q = pending(self.log)[0]
        writes_before = sum(1 for e in self.log.entries() if e["record"]["event"] == "CRM_WRITE")
        record_review(self.log, self.crm, q.key, "REJECT", "laksh@example.com")
        writes_after = sum(1 for e in self.log.entries() if e["record"]["event"] == "CRM_WRITE")
        self.assertEqual(writes_before, writes_after)

    def test_double_review_refused(self):
        self.pipe.handle(self.raw, compute_signature(self.raw, SECRET))
        q = pending(self.log)[0]
        record_review(self.log, self.crm, q.key, "APPROVE", "a@example.com")
        with self.assertRaises(ValueError):
            record_review(self.log, self.crm, q.key, "REJECT", "b@example.com")

    def test_anonymous_review_refused(self):
        self.pipe.handle(self.raw, compute_signature(self.raw, SECRET))
        q = pending(self.log)[0]
        with self.assertRaises(ValueError):
            record_review(self.log, self.crm, q.key, "APPROVE", "")

    def test_html_renders(self):
        self.pipe.handle(self.raw, compute_signature(self.raw, SECRET))
        out = render_html(self.log, self.dir / "review.html", 0.65)
        html = out.read_text()
        self.assertIn("ActionGate review queue", html)
        self.assertIn("chain intact", html)


class TestGateAndCorpus(unittest.TestCase):
    def test_threshold_boundary_is_inclusive(self):
        scorer = RuleScorer.load()
        ex = load("dev")[0]
        s = scorer.score(ex.item, ex.meeting)
        g = Gate(auto_commit_at=s.confidence)
        self.assertIs(g.decide(s).decision, Decision.AUTO_COMMIT)

    def test_every_corpus_item_is_labelled(self):
        for split in ("dev", "test"):
            ex = load(split)
            self.assertEqual(len(ex), 40, split)
            self.assertTrue(all(e.label in (0, 1) for e in ex))

    def test_labels_are_not_visible_to_the_classifier(self):
        """Feature extraction must not depend on anything outside the payload."""
        from actiongate.features import extract

        ex = load("test")[0]
        ev = extract(ex.item, ex.meeting)
        self.assertNotIn("label", ev.as_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
