"""Tests for alert taxonomy enums."""

from augur.data.enums import Disposition, Tactic


class TestDisposition:
    """Disposition must have exactly 5 values, and False Positive ≠ Benign Positive."""

    def test_disposition_has_five_values(self):
        assert len(Disposition) == 5

    def test_false_positive_is_distinct_from_benign_positive(self):
        assert Disposition.FALSE_POSITIVE != Disposition.BENIGN_POSITIVE

    def test_true_positive_critical_exists(self):
        assert Disposition.TRUE_POSITIVE_CRITICAL == "True Positive - Critical"

    def test_needs_investigation_exists(self):
        assert Disposition.TRUE_POSITIVE_POLICY == "True Positive - Policy Violation"


class TestTactic:
    """Tactic must have exactly 6 MITRE ATT&CK values in scope."""

    def test_tactic_has_six_values(self):
        assert len(Tactic) == 6

    def test_lateral_movement_exists(self):
        assert Tactic.LATERAL_MOVEMENT == "Lateral Movement"

    def test_defense_evasion_exists(self):
        assert Tactic.DEFENSE_EVASION == "Defense Evasion"
