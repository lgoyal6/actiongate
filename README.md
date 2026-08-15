# ActionGate

A confidence gate that sits between a Circleback webhook and a CRM write.

Circleback extracts action items from meetings and its automations can write them
straight into HubSpot, Salesforce, Attio or monday.com. This is a small receiver
that puts one step in between: score each extracted action item, auto-commit the
confident ones, and route the rest to a human. The premise is that a wrong action
item in a customer record is more expensive than a missing one, so the system
should gate on confidence rather than write everything it extracts.

Built against Circleback's published webhook schema, read 2026-08-14 at
`https://support.circleback.ai/en/articles/11014015-export-meeting-data-with-webhooks`.
Field names (`actionItems[].title`, `.assignee`, `transcript[].speaker`, ...) are
taken from that page, not invented.

## Run it

Python 3.12, standard library only. No models are downloaded and nothing calls out
to the network.

```bash
cd outputs/builds/circleback
uv venv --python 3.12 work/.venv          # already created if you ran the build
./work/.venv/bin/python -m actiongate.tests.test_actiongate   # 24 tests
./actiongate/demo.sh                                          # full walkthrough
./work/.venv/bin/python -m actiongate.cli eval                # precision/recall
```

`demo.sh` starts the receiver, refuses a forged signature, refuses a body edited
after signing, processes a correctly signed request, shows the review queue, has a
human approve an item, verifies the audit chain, tampers with the log and shows the
chain break, then prints the evaluation.

## What is in here

| file | what it does |
|---|---|
| `signature.py` | HMAC-SHA256 verification over the **raw** request bytes, constant-time compare |
| `schema.py` | Circleback's payload -> typed objects, absent-tolerant |
| `features.py` | 9 features: transcript grounding, commitment / hedge / conditional / retraction language, assignee resolution, temporal anchor, speaker identity, vague title |
| `classify.py` | `RuleScorer` (logistic model, real) and `LlmScorer` (pluggable, stubbed, SYNTHETIC) |
| `gate.py` | threshold policy: auto-commit at or above 0.65, otherwise review |
| `audit.py` | append-only hash-chained log; `verify_chain` finds the exact tampered record |
| `pipeline.py` | verify -> log -> score -> gate -> log -> CRM write, in that order |
| `review.py` | review queue projected from the log, plus a single-page HTML view |
| `server.py` | stdlib `http.server` receiver: 401 / 400 / 413 / 200 |
| `evaluate.py` | precision/recall sweep, Wilson intervals, cost-based threshold choice |
| `fit.py` | fits the logistic weights on the **dev split only** |
| `data/dev.json`, `data/test.json` | 16 hand-written labelled meetings, 80 action items |
| `probe_docs_verifier.js` | why this verifies raw bytes and the docs sample does not |

## The signature check

Circleback documents `x-signature` as hex HMAC-SHA256 of the request body, with a
`whsec_...` secret. That part is exactly right and is the same primitive Stripe and
GitHub use.

Their sample receiver hashes `JSON.stringify(req.body)`, which is the parsed body
re-serialized rather than the bytes that were signed. That round-trip is not
byte-preserving, so it rejects legitimate requests. `probe_docs_verifier.js`
demonstrates it: 5 of 7 correctly-signed bodies are rejected by the documented
approach and all 7 are accepted when the raw bytes are hashed. The failing cases
include a trailing-zero decimal (`800.00`, which appears in their own example
payload), any escaped non-ASCII character (relevant given 100+ languages), an
escaped emoji, pretty-printed whitespace, and exponent notation.

This receiver hashes `raw_body: bytes` and refuses a `str` argument outright, so the
mistake is not available to make. It also compares with `hmac.compare_digest`
instead of `===`.

Two things Circleback does not document and this cannot verify: any retry or
redelivery policy, and any timestamp header. Without a signed timestamp there is no
replay protection, so a captured request stays replayable forever. That is noted
rather than solved, because solving it needs a change on the sending side.

## Threshold policy

Two bands, nothing silently discarded:

- `confidence >= 0.65` -> `AUTO_COMMIT`, written to the CRM
- `confidence < 0.65` -> `REVIEW`, queued for a human, never written

0.65 was chosen on the dev split by minimising `k * (wrong CRM writes) + (items sent
to review)` with `k = 20`, i.e. one wrong row in a customer record is priced at
twenty human reviews. The choice is stable for any `k` between 5 and 100. See
`../RESULTS.md`.

## Audit log

One JSON object per line, each hashed over the previous hash, so editing or deleting
history breaks the chain at a reportable sequence number. Events: `WEBHOOK_ACCEPTED`,
`WEBHOOK_REJECTED`, `GATE_DECISION` (with the full feature vector and the supporting
transcript span), `CRM_WRITE` (with `authorised_by` = `gate` or `human:<email>`),
`REVIEW_DECISION`. A human decision appends a new record and never mutates the gate's.
The review queue is a projection of the log rather than a second mutable store.

## Honest limits

- The evaluation corpus is **SYNTHETIC**: 16 meetings written by hand for this
  exercise. Every number derived from it is labelled SYNTHETIC. It is small (80
  items), so precision is reported with 95% Wilson intervals.
- The same person wrote the classifier and the corpus, which biases results
  optimistically. The dev/test split exists to limit that: weights are fit on dev,
  the threshold is chosen on dev, and the reported numbers are from a test split
  that was not consulted while building either.
- Swapping the rule scorer for an LLM judge is a one-line change
  (`LlmScorer(complete_fn=your_model)`). It ships stubbed, and stub output is
  flagged `synthetic=True` so it can never be mistaken for a measurement.
