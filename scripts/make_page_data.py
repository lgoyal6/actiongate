"""Build the JSON the results page reads.

Calls the package's own sweep() with the committed weights, so the page shows
what evaluate.py computes rather than a reimplementation of it. Run from the
parent directory, because actiongate is a package and imports relatively:

    python3 -m actiongate.scripts.make_page_data
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from ..classify import RuleScorer
from ..evaluate import COST_RATIO_K, choose_by_cost, sweep

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "data"

# The grid evaluate.run() sweeps, not a finer one. A finer grid finds the same
# cost-minimising plateau and reports its lower edge, which would put a
# different threshold on the page than the one the repository publishes.
THRESHOLDS = [i / 20 for i in range(21)]

# The same ratios evaluate.run() reports sensitivity over. The README argues the
# choice is stable across them; the page lets a reader check that.
COST_RATIOS = [1, 5, 10, 20, 50, 100]


def main() -> None:
    scorer = RuleScorer.load()
    curves = {}
    for split in ("dev", "test"):
        pts = [dataclasses.asdict(p) for p in sweep(split, scorer, THRESHOLDS)]
        curves[split] = pts

    # Which threshold each cost ratio picks, chosen on dev and read off test,
    # which is the order that keeps the test split honest.
    dev_points = sweep("dev", scorer, THRESHOLDS)
    chosen = {str(k): choose_by_cost(dev_points, k).threshold for k in COST_RATIOS}

    payload = {
        "thresholds": THRESHOLDS,
        "curves": curves,
        "cost_ratios": COST_RATIOS,
        "chosen_on_dev": chosen,
        "default_cost_ratio": COST_RATIO_K,
        "scorer_id": scorer.scorer_id,
    }
    # The page runs the scorer itself, in pyodide, on a transcript a reader
    # types. These are copied verbatim rather than ported, so what scores your
    # input is what produced the sweep above.
    OUT.mkdir(parents=True, exist_ok=True)
    pkg = OUT / "actiongate"
    pkg.mkdir(exist_ok=True)
    for name in ("__init__.py", "classify.py", "features.py", "schema.py", "weights.json"):
        (pkg / name).write_text((ROOT / name).read_text())

    path = OUT / "sweep.json"
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1024:.1f} kB")
    for name, thr in (("commit everything", 0.0), ("gate", chosen[str(int(COST_RATIO_K))])):
        p = next(x for x in curves["test"] if abs(x["threshold"] - thr) < 1e-9)
        print(f"  test, {name:<18} thr {thr:.2f}  wrong {p['fp']:>2}  committed {p['tp']:>2}  "
              f"precision {p['precision']:.3f}")
    print("  thresholds chosen on dev by cost ratio:", chosen)


if __name__ == "__main__":
    main()
