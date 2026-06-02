"""Tests for the legacy eval agent (run_eval)."""

from uuid import uuid4

from augur.data.enums import Disposition, Tactic
from augur.data.schema import GroundTruth, TriageOutput
from augur.eval import EvalResult, TacticMetrics, run_eval


def _make_pair(
    tactic: Tactic,
    disposition: Disposition = Disposition.TRUE_POSITIVE_CRITICAL,
    pred_tactic: Tactic | None = None,
    pred_disposition: Disposition | None = None,
) -> tuple[TriageOutput, GroundTruth]:
    aid = uuid4()
    gt = GroundTruth(
        alert_id=aid,
        disposition=disposition,
        attack_tactic=tactic if disposition != Disposition.FALSE_POSITIVE else None,
        attack_technique="T1110.001",
    )
    pred = TriageOutput(
        alert_id=aid,
        disposition=pred_disposition or disposition,
        attack_tactic=pred_tactic if pred_tactic is not None else gt.attack_tactic,
        confidence=0.9,
        severity="High",
        reasoning="test",
        trace_id="t-1",
    )
    return pred, gt


class TestRunEvalEmpty:
    def test_empty_predictions_returns_empty_per_tactic(self):
        result = run_eval([], [], eval_run_id="e-1")
        assert result.per_tactic == {}
        assert result.flagged_tactic is None
        assert result.batch_size == 0

    def test_mismatched_alert_ids_are_skipped(self):
        pred = TriageOutput(
            alert_id=uuid4(),
            disposition=Disposition.FALSE_POSITIVE,
            confidence=0.5,
            severity="Low",
            trace_id="t-1",
        )
        gt = GroundTruth(
            alert_id=uuid4(),
            disposition=Disposition.FALSE_POSITIVE,
        )
        result = run_eval([pred], [gt], eval_run_id="e-2")
        assert result.per_tactic == {}


class TestRunEvalAllCorrect:
    def test_all_correct_gives_f1_one(self):
        pairs = [_make_pair(Tactic.CREDENTIAL_ACCESS) for _ in range(10)]
        preds = [p for p, _ in pairs]
        gts = [g for _, g in pairs]
        result = run_eval(preds, gts, eval_run_id="e-3")
        metrics = result.per_tactic["Credential Access"]
        assert metrics.n_correct == 10
        assert metrics.precision == 1.0
        assert metrics.recall == 1.0
        assert metrics.f1 == 1.0
        assert metrics.failure_trace_ids == []
        assert result.flagged_tactic is None


class TestRunEvalAllWrong:
    def test_all_wrong_gives_f1_zero(self):
        pairs = [
            _make_pair(
                Tactic.LATERAL_MOVEMENT,
                pred_tactic=Tactic.CREDENTIAL_ACCESS,
            )
            for _ in range(10)
        ]
        preds = [p for p, _ in pairs]
        gts = [g for _, g in pairs]
        result = run_eval(preds, gts, eval_run_id="e-4")
        metrics = result.per_tactic["Lateral Movement"]
        assert metrics.n_correct == 0
        assert metrics.recall == 0.0
        assert metrics.f1 == 0.0
        assert len(metrics.failure_trace_ids) == 10

    def test_all_wrong_flags_tactic_when_above_threshold(self):
        pairs = [
            _make_pair(
                Tactic.LATERAL_MOVEMENT,
                pred_tactic=Tactic.CREDENTIAL_ACCESS,
            )
            for _ in range(10)
        ]
        preds = [p for p, _ in pairs]
        gts = [g for _, g in pairs]
        result = run_eval(preds, gts, eval_run_id="e-5")
        assert result.flagged_tactic == Tactic.LATERAL_MOVEMENT


class TestRunEvalThreshold:
    def test_below_five_samples_not_flagged(self):
        pairs = [
            _make_pair(
                Tactic.LATERAL_MOVEMENT,
                pred_tactic=Tactic.CREDENTIAL_ACCESS,
            )
            for _ in range(4)
        ]
        preds = [p for p, _ in pairs]
        gts = [g for _, g in pairs]
        result = run_eval(preds, gts, eval_run_id="e-6")
        assert result.flagged_tactic is None

    def test_high_f1_not_flagged(self):
        pairs = [_make_pair(Tactic.INITIAL_ACCESS) for _ in range(10)]
        preds = [p for p, _ in pairs]
        gts = [g for _, g in pairs]
        result = run_eval(preds, gts, eval_run_id="e-7")
        assert result.flagged_tactic is None


class TestPrecisionRecallDiffer:
    def test_precision_and_recall_differ_with_misclassifications(self):
        preds = []
        gts = []
        # 5 truly LM, all predicted correctly
        for _ in range(5):
            p, g = _make_pair(Tactic.LATERAL_MOVEMENT)
            preds.append(p)
            gts.append(g)
        # 3 truly CA, but predicted as LM (wrong)
        for _ in range(3):
            p, g = _make_pair(
                Tactic.CREDENTIAL_ACCESS,
                pred_tactic=Tactic.LATERAL_MOVEMENT,
            )
            preds.append(p)
            gts.append(g)

        result = run_eval(preds, gts, eval_run_id="e-8")
        lm = result.per_tactic["Lateral Movement"]
        # Recall for LM: 5/5 = 1.0 (all true LM were caught)
        assert lm.recall == 1.0
        # Precision for LM: 5/8 = 0.625 (5 correct out of 8 predicted as LM)
        assert abs(lm.precision - 5 / 8) < 0.01
        assert lm.precision != lm.recall


class TestTacticMetricsAccuracy:
    def test_accuracy_property(self):
        m = TacticMetrics(n_total=10, n_correct=7)
        assert m.accuracy == 0.7

    def test_accuracy_zero_total(self):
        m = TacticMetrics(n_total=0, n_correct=0)
        assert m.accuracy == 0.0
