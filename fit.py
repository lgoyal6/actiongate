"""Fit the logistic model on the dev split only. Pure stdlib gradient descent.

Deterministic: fixed initialisation at zero, fixed learning rate, fixed epoch
count, no shuffling.  Two runs give bit-identical weights.

The test split is never touched here.  That separation is the only reason the
numbers in RESULTS.md mean anything, given that the same person wrote the
classifier and the corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

from .classify import WEIGHTS_PATH, sigmoid
from .corpus import load
from .features import FEATURE_NAMES, extract

EPOCHS = 4000
LR = 0.35
L2 = 0.01


def fit(split: str = "dev") -> dict:
    examples = load(split)
    X = [extract(e.item, e.meeting).vector() for e in examples]
    y = [float(e.label) for e in examples]
    n, d = len(X), len(FEATURE_NAMES)
    w = [0.0] * d
    b = 0.0

    for _ in range(EPOCHS):
        gw = [0.0] * d
        gb = 0.0
        for xi, yi in zip(X, y):
            p = sigmoid(b + sum(w[j] * xi[j] for j in range(d)))
            err = p - yi
            for j in range(d):
                gw[j] += err * xi[j]
            gb += err
        for j in range(d):
            w[j] -= LR * (gw[j] / n + L2 * w[j])
        b -= LR * (gb / n)

    blob = {
        "scorer_id": "rules-logreg-v1",
        "fit_on": split,
        "n_examples": n,
        "epochs": EPOCHS,
        "lr": LR,
        "l2": L2,
        "bias": round(b, 6),
        "weights": {name: round(w[j], 6) for j, name in enumerate(FEATURE_NAMES)},
    }
    Path(WEIGHTS_PATH).write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
    return blob


if __name__ == "__main__":
    print(json.dumps(fit(), indent=2))
