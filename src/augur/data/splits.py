"""Stratified train/dev/test split for Augur alert datasets.

Stratifies by ground-truth attack_tactic so each split contains a
representative tactic mix. Test set is held out — no prompt-tuning or
eval scoring touches it during development per spec D4.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import TypeAlias

from augur.data.schema import Alert, GroundTruth

Pair: TypeAlias = tuple[Alert, GroundTruth]


def split_pairs(
    pairs: list[Pair],
    test_frac: float = 0.2,
    dev_frac: float = 0.1,
    seed: int = 0,
) -> tuple[list[Pair], list[Pair], list[Pair]]:
    """Return (train, dev, test) splits stratified by attack_tactic.

    Sums to len(pairs) (rounding may shift one item between splits).
    """
    if test_frac + dev_frac >= 1.0:
        raise ValueError("test_frac + dev_frac must be < 1.0")

    rng = random.Random(seed)
    by_tactic: dict[str, list[Pair]] = defaultdict(list)
    for a, gt in pairs:
        key = gt.attack_tactic.value if gt.attack_tactic is not None else "Not Applicable"
        by_tactic[key].append((a, gt))

    train: list[Pair] = []
    dev: list[Pair] = []
    test: list[Pair] = []

    for _tactic, group in by_tactic.items():
        rng.shuffle(group)
        n = len(group)
        n_test = round(n * test_frac)
        n_dev = round(n * dev_frac)
        test.extend(group[:n_test])
        dev.extend(group[n_test : n_test + n_dev])
        train.extend(group[n_test + n_dev :])

    rng.shuffle(train)
    rng.shuffle(dev)
    rng.shuffle(test)
    return train, dev, test
