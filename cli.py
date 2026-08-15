"""One entry point for everything: python -m actiongate.cli <command>."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import AuditLog
from .classify import RuleScorer
from .corpus import load
from .evaluate import choose_operating_point, markdown_table, pr_plot, run
from .fit import fit
from .gate import Gate, PRECISION_TARGET
from .pipeline import CrmSink, SignatureError, default_pipeline
from .review import pending, record_review, render_html, reviewed
from .schema import Meeting
from .signature import compute_signature

DEFAULT_STATE = Path("state")
DEFAULT_SECRET = "whsec_demo_do_not_use_in_production"


def _log(args) -> AuditLog:
    return AuditLog(Path(args.state) / "audit.jsonl")


def _crm(args) -> CrmSink:
    return CrmSink(Path(args.state) / "crm.jsonl")


def cmd_serve(args) -> int:
    from .server import serve

    serve(Path(args.state), args.secret, args.host, args.port, args.threshold)
    return 0


def cmd_sign(args) -> int:
    raw = Path(args.file).read_bytes()
    print(compute_signature(raw, args.secret))
    return 0


def cmd_ingest(args) -> int:
    """Run a payload file through the pipeline in-process (no HTTP)."""
    raw = Path(args.file).read_bytes()
    sig = args.signature or compute_signature(raw, args.secret)
    pipe = default_pipeline(Path(args.state), args.secret, args.threshold)
    try:
        summary = pipe.handle(raw, sig)
    except SignatureError:
        print("REJECTED: invalid x-signature (nothing was classified or written)")
        return 1
    print(json.dumps(summary, indent=2))
    return 0


def cmd_ingest_corpus(args) -> int:
    """Feed every meeting in a labelled split through the live pipeline."""
    blob = json.loads((Path(__file__).with_name("data") / f"{args.split}.json").read_text())
    pipe = default_pipeline(Path(args.state), args.secret, args.threshold)
    totals = {"meetings": 0, "action_items": 0, "auto_committed": 0, "queued_for_review": 0}
    for entry in blob:
        raw = json.dumps(entry["payload"]).encode()
        summary = pipe.handle(raw, compute_signature(raw, args.secret))
        totals["meetings"] += 1
        for k in ("action_items", "auto_committed", "queued_for_review"):
            totals[k] += summary[k]
    print(json.dumps(totals, indent=2))
    return 0


def cmd_queue(args) -> int:
    items = pending(_log(args))
    if not items:
        print("review queue is empty")
        return 0
    # Show the threshold the items were actually gated at, not the current default.
    thresholds = sorted({i.threshold for i in items})
    shown = ", ".join(f"{t:.2f}" for t in thresholds)
    print(f"{len(items)} item(s) awaiting review (gated at threshold {shown})\n")
    for i in items:
        print(f"  {i.confidence:.3f}  {i.key}")
        print(f"         {i.title}")
        print(f"         why: {i.reason}")
        span = i.evidence.get("grounding_span", "")
        if span:
            print(f'         span: {i.evidence.get("grounding_speaker","")}: "{span[:110]}"')
        print()
    return 0


def cmd_show(args) -> int:
    for i in pending(_log(args)) + reviewed(_log(args)):
        if i.key == args.key:
            print(json.dumps({
                "key": i.key, "title": i.title, "description": i.description,
                "assignee": i.assignee, "confidence": i.confidence,
                "threshold": i.threshold, "reason": i.reason,
                "evidence": i.evidence, "reviewed": i.reviewed,
            }, indent=2))
            return 0
    print(f"{args.key} not found", file=sys.stderr)
    return 1


def _review(args, verdict: str) -> int:
    try:
        entry = record_review(_log(args), _crm(args), args.key, verdict, args.reviewer, args.note)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{verdict} recorded at audit seq {entry['seq']} by {args.reviewer}")
    return 0


def cmd_approve(args) -> int:
    return _review(args, "APPROVE")


def cmd_reject(args) -> int:
    return _review(args, "REJECT")


def cmd_html(args) -> int:
    out = render_html(_log(args), Path(args.out), args.threshold or Gate().auto_commit_at)
    print(f"wrote {out}")
    return 0


def cmd_audit(args) -> int:
    log = _log(args)
    if args.tail:
        for entry in list(log.entries())[-args.tail:]:
            print(json.dumps(entry["record"], sort_keys=True))
    ok, msg = log.verify_chain()
    print(("OK   " if ok else "FAIL ") + msg)
    return 0 if ok else 1


def cmd_fit(args) -> int:
    print(json.dumps(fit(args.split), indent=2))
    return 0


def cmd_eval(args) -> int:
    report = run(args.precision_target, args.k, Path(args.out) if args.out else None)
    c = report["corpus"]
    print("CORPUS (SYNTHETIC: transcripts written by hand for this evaluation)")
    for split in ("dev", "test"):
        s = c[split]
        print(f"  {split:4s} {s['meetings']:2d} meetings, {s['action_items']:3d} action items, "
              f"{s['positives']} genuine / {s['negatives']} not, {s['hard_cases']} hard cases")
    print(f"\nDEV sweep (weights fit on dev; threshold chosen here) -- SYNTHETIC corpus")
    print(markdown_table(_as_points(report["dev_sweep"])))
    print(f"\nHELD-OUT TEST sweep -- SYNTHETIC corpus")
    print(markdown_table(_as_points(report["test_sweep"])))
    print("\nPrecision-recall tradeoff on the held-out test split (SYNTHETIC corpus)")
    print(pr_plot(_as_points(report["test_sweep"])))
    t = report["chosen_threshold"]
    d, e, b = report["dev_at_chosen"], report["test_at_chosen"], report["test_baseline_no_gate"]
    print(f"\nOPERATING POINT: auto-commit at confidence >= {t:.2f}")
    print(f"  rule            : {report['chosen_rule']}")
    print(f"  chosen on dev   : precision {d['precision']:.3f} "
          f"(95% CI {d['precision_lo']:.2f}-{d['precision_hi']:.2f}), recall {d['recall']:.3f}, "
          f"review rate {d['review_rate']:.3f}, wrong CRM writes {d['fp']}")
    print(f"  held-out test   : precision {e['precision']:.3f} "
          f"(95% CI {e['precision_lo']:.2f}-{e['precision_hi']:.2f}), recall {e['recall']:.3f}, "
          f"review rate {e['review_rate']:.3f}, wrong CRM writes {e['fp']}")
    print(f"\nBASELINE on the same held-out split: write every extracted item, no gate")
    print(f"  precision {b['precision']:.3f}, recall {b['recall']:.3f}, "
          f"review rate {b['review_rate']:.3f}, wrong CRM writes {b['fp']}")
    print(f"  => the gate takes wrong CRM writes from {b['fp']} to {e['fp']} out of "
          f"{report['corpus']['test']['action_items']} action items, and still auto-commits "
          f"{e['tp']} of {report['corpus']['test']['positives']} genuine ones.")
    print(f"\nThreshold chosen on dev as a function of the cost ratio k "
          f"(k = wrong CRM row, priced in human reviews):")
    for kk, th in report["threshold_by_cost_ratio"].items():
        print(f"  k={kk:>4}  ->  threshold {th:.2f}")
    return 0


def _as_points(rows):
    from .evaluate import Point
    return [Point(**r) for r in rows]


def cmd_corpus(args) -> int:
    for split in ("dev", "test"):
        ex = load(split)
        print(f"--- {split}: {len(ex)} items")
        for e in ex:
            print(f"  [{e.label}]{'H' if e.hard else ' '} {e.meeting.id}:{e.item.id}  {e.item.title}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="actiongate", description=__doc__)
    p.add_argument("--state", default=str(DEFAULT_STATE), help="directory for audit.jsonl and crm.jsonl")
    p.add_argument("--secret", default=DEFAULT_SECRET, help="Circleback signing secret (whsec_...)")
    p.add_argument("--threshold", type=float, default=None, help="auto-commit threshold override")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the webhook receiver")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("sign", help="print the x-signature for a payload file")
    s.add_argument("file")
    s.set_defaults(func=cmd_sign)

    s = sub.add_parser("ingest", help="run one payload file through the pipeline")
    s.add_argument("file")
    s.add_argument("--signature", default=None, help="use a wrong value here to see the 401 path")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("ingest-corpus", help="run a whole labelled split through the pipeline")
    s.add_argument("--split", default="test", choices=["dev", "test"])
    s.set_defaults(func=cmd_ingest_corpus)

    s = sub.add_parser("queue", help="list items awaiting human review")
    s.set_defaults(func=cmd_queue)

    s = sub.add_parser("show", help="show one queued item with its evidence")
    s.add_argument("key")
    s.set_defaults(func=cmd_show)

    for name, fn in (("approve", cmd_approve), ("reject", cmd_reject)):
        s = sub.add_parser(name, help=f"{name} a queued item (appends to the audit log)")
        s.add_argument("key")
        s.add_argument("--reviewer", required=True)
        s.add_argument("--note", default="")
        s.set_defaults(func=fn)

    s = sub.add_parser("html", help="render the review queue as a single HTML page")
    s.add_argument("--out", default="review.html")
    s.set_defaults(func=cmd_html)

    s = sub.add_parser("audit", help="verify the audit hash chain")
    s.add_argument("--tail", type=int, default=0)
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("fit", help="fit classifier weights on the dev split")
    s.add_argument("--split", default="dev")
    s.set_defaults(func=cmd_fit)

    s = sub.add_parser("eval", help="precision/recall sweep and threshold choice")
    s.add_argument("--precision-target", type=float, default=PRECISION_TARGET)
    s.add_argument("--k", type=float, default=20.0, help="cost of one wrong CRM row, in human reviews")
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_eval)

    s = sub.add_parser("corpus", help="list the labelled corpus")
    s.set_defaults(func=cmd_corpus)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
