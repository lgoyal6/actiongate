"""Precision / recall of the auto-commit band as a function of the threshold.

Definitions, stated because they are easy to get backwards:

  A *positive prediction* is AUTO_COMMIT (confidence >= threshold).
  TP  auto-committed and the item was a genuine commitment.
  FP  auto-committed and it was not. This is the expensive error: a wrong action
      item now sits in a customer record.
  FN  held for review even though it was genuine. Costs one queue row.

  precision = TP / (TP + FP)   how much of what we wrote to the CRM was correct
  recall    = TP / (TP + FN)   how much of the genuine work got in without a human

``review_rate`` is the operational cost: the share of all items a human must look
at.  ``review_precision`` is what fraction of the queue is genuinely actionable,
i.e. whether the queue is worth a human's attention or is mostly noise.

Precision is reported with a Wilson 95% interval because the corpus is small and
a bare point estimate on ~40 items would overstate what is known.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from .classify import RuleScorer, Scorer
from .corpus import load, summarise


@dataclass
class Point:
    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    precision_lo: float
    precision_hi: float
    recall: float
    f1: float
    auto_commit_rate: float
    review_rate: float
    review_precision: float


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a proportion. Returns (0,1) when n == 0."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def sweep(split: str, scorer: Scorer, thresholds: list[float]) -> list[Point]:
    examples = load(split)
    scored = [(scorer.score(e.item, e.meeting).confidence, e.label) for e in examples]
    total = len(scored)
    points = []
    for t in thresholds:
        tp = sum(1 for c, y in scored if c >= t and y == 1)
        fp = sum(1 for c, y in scored if c >= t and y == 0)
        fn = sum(1 for c, y in scored if c < t and y == 1)
        tn = sum(1 for c, y in scored if c < t and y == 0)
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        lo, hi = wilson(tp, tp + fp)
        queued = fn + tn
        points.append(
            Point(
                threshold=round(t, 3),
                tp=tp, fp=fp, fn=fn, tn=tn,
                precision=round(prec, 4),
                precision_lo=round(lo, 4),
                precision_hi=round(hi, 4),
                recall=round(rec, 4),
                f1=round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0,
                auto_commit_rate=round((tp + fp) / total, 4),
                review_rate=round(queued / total, 4),
                review_precision=round(fn / queued, 4) if queued else 0.0,
            )
        )
    return points


def expected_cost(p: Point, k: float) -> float:
    """Cost of an operating point, denominated in human reviews.

    ``k`` is the only judgement call in the whole evaluation, stated out loud:
    how many minutes of human review one wrong CRM row is worth.  Everything
    below the threshold is reviewed by a person, so the cost is

        k * (wrong auto-commits)  +  1 * (items sent to review)

    A false negative is not a separate term because it is not lost: it lands in
    the queue and a human approves it.  It costs a review, same as a true
    negative does.  This is what makes the asymmetry explicit rather than hiding
    it inside an arbitrary precision target.
    """
    return k * p.fp + (p.fn + p.tn)


def choose_by_cost(points: list[Point], k: float) -> Point:
    """Minimum expected cost; ties broken toward higher recall, then lower threshold."""
    return min(points, key=lambda p: (expected_cost(p, k), -p.recall, p.threshold))


def choose_operating_point(points: list[Point], precision_target: float) -> Point:
    """Alternative rule, kept for comparison: clear a precision target on the
    lower confidence bound if any threshold can, else on the point estimate."""
    eligible = [p for p in points if p.precision_lo >= precision_target]
    if not eligible:
        eligible = [p for p in points if p.precision >= precision_target]
    if not eligible:
        return max(points, key=lambda p: p.f1)
    return max(eligible, key=lambda p: (p.recall, -p.threshold))


def markdown_table(points: list[Point]) -> str:
    head = (
        "| threshold | TP | FP | FN | TN | precision | precision 95% CI | recall | F1 "
        "| auto-commit rate | review rate |\n|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [
        f"| {p.threshold:.2f} | {p.tp} | {p.fp} | {p.fn} | {p.tn} | {p.precision:.3f} "
        f"| {p.precision_lo:.2f}-{p.precision_hi:.2f} | {p.recall:.3f} | {p.f1:.3f} "
        f"| {p.auto_commit_rate:.3f} | {p.review_rate:.3f} |"
        for p in points
    ]
    return "\n".join([head, *rows])


def pr_plot(points: list[Point], width: int = 46) -> str:
    """ASCII precision-recall curve, because a picture of the tradeoff is the point."""
    rows = ["  precision", "  1.00 " + "-" * width]
    for lo in [0.95, 0.90, 0.85, 0.80, 0.70, 0.60, 0.50]:
        band = [p for p in points if lo <= p.precision]
        best = max(band, key=lambda p: p.recall) if band else None
        if best is None:
            rows.append(f"  {lo:.2f} |")
            continue
        col = int(best.recall * width)
        rows.append(f"  {lo:.2f} |{' ' * col}*  r={best.recall:.2f} @ t={best.threshold:.2f}")
    rows.append("       +" + "-" * width)
    rows.append("        0.0" + " " * (width - 8) + "recall 1.0")
    return "\n".join(rows)


# One wrong CRM row is assumed to be worth this many human reviews. Stated as an
# assumption, not a measurement; change it and the chosen threshold moves.
COST_RATIO_K = 20.0


def run(precision_target: float = 0.95, k: float = COST_RATIO_K,
        out: Path | None = None) -> dict:
    scorer = RuleScorer.load()
    thresholds = [i / 20 for i in range(21)]
    dev = sweep("dev", scorer, thresholds)
    test = sweep("test", scorer, thresholds)

    chosen_dev = choose_by_cost(dev, k)
    at_test = next(p for p in test if abs(p.threshold - chosen_dev.threshold) < 1e-9)
    # The no-gate baseline: write every extracted action item straight to the CRM.
    baseline_test = next(p for p in test if p.threshold == 0.0)

    sensitivity = {
        str(kk): choose_by_cost(dev, kk).threshold for kk in (1, 5, 10, 20, 50, 100)
    }

    report = {
        "scorer_id": scorer.scorer_id,
        "corpus": {"dev": summarise("dev"), "test": summarise("test")},
        "cost_ratio_k": k,
        "chosen_threshold": chosen_dev.threshold,
        "chosen_on": "dev",
        "chosen_rule": f"minimise {k:.0f}*FP + reviews on the dev split",
        "dev_at_chosen": asdict(chosen_dev),
        "test_at_chosen": asdict(at_test),
        "test_baseline_no_gate": asdict(baseline_test),
        "wrong_crm_writes_baseline": baseline_test.fp,
        "wrong_crm_writes_gated": at_test.fp,
        "threshold_by_cost_ratio": sensitivity,
        "precision_target_alternative": {
            "target": precision_target,
            "threshold": choose_operating_point(dev, precision_target).threshold,
        },
        "dev_sweep": [asdict(p) for p in dev],
        "test_sweep": [asdict(p) for p in test],
        "corpus_is_synthetic": True,
    }
    if out:
        Path(out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
