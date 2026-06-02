"""Edge case tests for existing modules that lack boundary coverage."""

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from augur.agents.triage import _parse_agent_response
from augur.data.enums import Disposition, Tactic
from augur.data.schema import GroundTruth, TriageOutput, Severity
from augur.data.splits import split_pairs
from augur.data.synthetic import generate_alert_batch, iter_alerts


class TestSyntheticEdgeCases:
    def test_batch_size_zero(self):
        alerts, truths = generate_alert_batch(n=0)
        assert alerts == []
        assert truths == []

    def test_batch_size_one(self):
        alerts, truths = generate_alert_batch(n=1)
        assert len(alerts) == 1
        assert alerts[0].alert_id == truths[0].alert_id

    def test_iter_alerts_zero(self):
        assert list(iter_alerts(n=0)) == []


class TestSplitsEdgeCases:
    def test_empty_input(self):
        train, dev, test = split_pairs([])
        assert train == []
        assert dev == []
        assert test == []

    def test_single_item(self):
        pairs = list(zip(*generate_alert_batch(n=1)))
        train, dev, test = split_pairs(pairs, test_frac=0.2, dev_frac=0.1)
        assert len(train) + len(dev) + len(test) == 1

    def test_invalid_fractions_raise(self):
        pairs = list(zip(*generate_alert_batch(n=10)))
        with pytest.raises(ValueError, match="< 1.0"):
            split_pairs(pairs, test_frac=0.6, dev_frac=0.5)

    def test_different_seeds_differ(self):
        pairs = list(zip(*generate_alert_batch(n=100)))
        a1, _, _ = split_pairs(pairs, seed=1)
        a2, _, _ = split_pairs(pairs, seed=2)
        ids1 = [p[0].alert_id for p in a1]
        ids2 = [p[0].alert_id for p in a2]
        assert ids1 != ids2


class TestSchemaEdgeCases:
    def test_confidence_at_boundaries(self):
        out = TriageOutput(
            alert_id=uuid4(),
            disposition=Disposition.FALSE_POSITIVE,
            confidence=0.0,
            severity=Severity.LOW,
            trace_id="t-1",
        )
        assert out.confidence == 0.0

        out2 = TriageOutput(
            alert_id=uuid4(),
            disposition=Disposition.FALSE_POSITIVE,
            confidence=1.0,
            severity=Severity.LOW,
            trace_id="t-2",
        )
        assert out2.confidence == 1.0

    def test_negative_confidence_raises(self):
        with pytest.raises(ValidationError):
            TriageOutput(
                alert_id=uuid4(),
                disposition=Disposition.FALSE_POSITIVE,
                confidence=-0.1,
                severity=Severity.LOW,
                trace_id="t-3",
            )

    def test_needs_investigation_requires_tactic(self):
        with pytest.raises(ValidationError):
            TriageOutput(
                alert_id=uuid4(),
                disposition=Disposition.NEEDS_INVESTIGATION,
                confidence=0.5,
                severity=Severity.MEDIUM,
                trace_id="t-4",
            )

    def test_benign_positive_requires_tactic(self):
        with pytest.raises(ValidationError):
            TriageOutput(
                alert_id=uuid4(),
                disposition=Disposition.BENIGN_POSITIVE,
                confidence=0.5,
                severity=Severity.MEDIUM,
                trace_id="t-5",
            )

    def test_ground_truth_fp_allows_no_tactic(self):
        gt = GroundTruth(
            alert_id=uuid4(),
            disposition=Disposition.FALSE_POSITIVE,
        )
        assert gt.attack_tactic is None


class TestEnumEdgeCases:
    def test_invalid_disposition_raises(self):
        with pytest.raises(ValueError):
            Disposition("Not A Real Disposition")

    def test_invalid_tactic_raises(self):
        with pytest.raises(ValueError):
            Tactic("Not A Real Tactic")

    def test_needs_investigation_value(self):
        assert Disposition.NEEDS_INVESTIGATION == "Needs Investigation"


class TestParseAgentResponseEdgeCases:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty response"):
            _parse_agent_response("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty response"):
            _parse_agent_response("   \n  ")

    def test_no_json_in_prose_raises(self):
        with pytest.raises(ValueError, match="No JSON"):
            _parse_agent_response("This is just prose with no JSON at all.")

    def test_json_embedded_in_prose(self):
        raw = 'Here is the result:\n{"disposition": "False Positive"}\nDone.'
        result = _parse_agent_response(raw)
        assert result["disposition"] == "False Positive"
