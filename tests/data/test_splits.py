"""Tests for stratified train/dev/test splitting."""

from augur.data.splits import split_pairs
from augur.data.synthetic import generate_alert_batch


def test_split_proportions_sum_to_input():
    alerts, truths = generate_alert_batch(n=100)
    pairs = list(zip(alerts, truths))
    train, dev, test = split_pairs(pairs, test_frac=0.2, dev_frac=0.1, seed=42)
    assert len(train) + len(dev) + len(test) == 100


def test_split_test_fraction_respected_within_margin():
    """Stratified split keeps test/dev within a small tolerance."""
    pairs = list(zip(*generate_alert_batch(n=100)))
    train, dev, test = split_pairs(pairs, test_frac=0.2, dev_frac=0.1, seed=42)
    # Stratifying by tactic means per-bucket rounding shifts counts ±2 total
    assert abs(len(test) - 20) <= 5
    assert abs(len(dev) - 10) <= 5
    assert len(train) == 100 - len(test) - len(dev)


def test_split_alert_ids_disjoint():
    pairs = list(zip(*generate_alert_batch(n=200)))
    train, dev, test = split_pairs(pairs, test_frac=0.2, dev_frac=0.1, seed=42)
    train_ids = {a.alert_id for a, _ in train}
    dev_ids = {a.alert_id for a, _ in dev}
    test_ids = {a.alert_id for a, _ in test}
    assert train_ids.isdisjoint(dev_ids)
    assert train_ids.isdisjoint(test_ids)
    assert dev_ids.isdisjoint(test_ids)


def test_split_is_deterministic_with_same_seed():
    pairs = list(zip(*generate_alert_batch(n=100)))
    a1, _, _ = split_pairs(pairs, test_frac=0.2, dev_frac=0.1, seed=42)
    a2, _, _ = split_pairs(pairs, test_frac=0.2, dev_frac=0.1, seed=42)
    assert [p[0].alert_id for p in a1] == [p[0].alert_id for p in a2]


def test_split_stratifies_by_tactic_when_possible():
    """With 60+ alerts spanning multiple tactics, each split should have at least 2 tactics."""
    pairs = list(zip(*generate_alert_batch(n=200)))
    train, dev, test = split_pairs(pairs, test_frac=0.2, dev_frac=0.1, seed=42)
    for split in (train, dev, test):
        tactics = {gt.attack_tactic for _, gt in split if gt.attack_tactic is not None}
        assert len(tactics) >= 2
