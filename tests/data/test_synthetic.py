"""Tests for synthetic alert generator."""

from augur.data.enums import Disposition, Tactic
from augur.data.synthetic import generate_alert_batch, iter_alerts


class TestGenerateAlertBatch:
    def test_default_batch_size(self):
        alerts, truths = generate_alert_batch()
        assert len(alerts) == 25
        assert len(truths) == 25

    def test_alert_ids_match_ground_truth(self):
        alerts, truths = generate_alert_batch(n=10)
        for alert, gt in zip(alerts, truths):
            assert alert.alert_id == gt.alert_id

    def test_all_tactics_represented(self):
        _, truths = generate_alert_batch(n=50)
        tactics = {gt.attack_tactic for gt in truths if gt.attack_tactic is not None}
        assert len(tactics) == len(Tactic)

    def test_all_dispositions_present(self):
        _, truths = generate_alert_batch(n=100)
        dispositions = {gt.disposition for gt in truths}
        # Deterministic distribution guarantees at least some of each
        assert Disposition.FALSE_POSITIVE in dispositions
        assert Disposition.TRUE_POSITIVE_CRITICAL in dispositions
        assert Disposition.BENIGN_POSITIVE in dispositions


class TestIterAlerts:
    def test_lazy_iteration(self):
        pairs = list(iter_alerts(n=5))
        assert len(pairs) == 5
        assert pairs[0][0].source == "synthetic"
