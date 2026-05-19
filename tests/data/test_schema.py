"""Tests for alert, ground truth, and triage output schemas."""

import pytest
from pydantic import ValidationError

from augur.data.enums import Disposition, Tactic
from augur.data.schema import (
    Alert,
    AlertContext,
    GroundTruth,
    RawSignals,
    Severity,
    TriageOutput,
)


class TestAlertConstruction:
    def test_minimal_alert_succeeds(self):
        alert = Alert(
            raw_signals=RawSignals(src_ip="10.0.0.1", dst_ip="10.0.0.2", dst_port=443)
        )
        assert alert.source == "synthetic"
        assert alert.raw_signals.dst_port == 443

    def test_alert_id_is_uuid(self):
        alert = Alert(
            raw_signals=RawSignals(src_ip="10.0.0.1", dst_ip="10.0.0.2", dst_port=80)
        )
        assert alert.alert_id is not None


class TestGroundTruth:
    def test_ground_truth_pairs_with_alert(self):
        alert = Alert(
            raw_signals=RawSignals(src_ip="10.0.0.1", dst_ip="10.0.0.2", dst_port=445)
        )
        gt = GroundTruth(
            alert_id=alert.alert_id,
            disposition=Disposition.TRUE_POSITIVE_CRITICAL,
            attack_tactic=Tactic.LATERAL_MOVEMENT,
            attack_technique="T1021.002",
        )
        assert gt.alert_id == alert.alert_id


class TestTriageOutput:
    def test_valid_triage_output_succeeds(self):
        from uuid import uuid4

        out = TriageOutput(
            alert_id=uuid4(),
            disposition=Disposition.TRUE_POSITIVE_CRITICAL,
            attack_tactic=Tactic.LATERAL_MOVEMENT,
            confidence=0.92,
            severity=Severity.HIGH,
            reasoning="SMB admin share access from unusual source",
            trace_id="abc123",
        )
        assert out.disposition == Disposition.TRUE_POSITIVE_CRITICAL

    def test_false_positive_allows_no_tactic(self):
        from uuid import uuid4

        out = TriageOutput(
            alert_id=uuid4(),
            disposition=Disposition.FALSE_POSITIVE,
            confidence=0.7,
            reasoning="No matching attack pattern",
            trace_id="def456",
        )
        assert out.attack_tactic is None

    def test_non_fp_without_tactic_raises(self):
        from uuid import uuid4

        with pytest.raises(ValidationError):
            TriageOutput(
                alert_id=uuid4(),
                disposition=Disposition.TRUE_POSITIVE_CRITICAL,
                confidence=0.9,
                trace_id="ghi789",
            )

    def test_confidence_out_of_range_raises(self):
        from uuid import uuid4

        with pytest.raises(ValidationError):
            TriageOutput(
                alert_id=uuid4(),
                disposition=Disposition.FALSE_POSITIVE,
                confidence=1.5,
                trace_id="jkl012",
            )
