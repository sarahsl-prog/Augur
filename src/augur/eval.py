"""Eval Agent — score triage results against ground truth, flag worst tactic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from augur.data.enums import Disposition, Tactic
from augur.data.schema import GroundTruth, TriageOutput


@dataclass
class TacticMetrics:
    n_total: int = 0
    n_correct: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    failure_trace_ids: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.n_correct / self.n_total if self.n_total else 0.0


@dataclass
class EvalResult:
    eval_run_id: str
    batch_size: int
    per_tactic: dict[str, TacticMetrics]
    flagged_tactic: Tactic | None = None


def run_eval(
    predictions: list[TriageOutput],
    ground_truths: list[GroundTruth],
    eval_run_id: str = "",
) -> EvalResult:
    """Compare predictions vs ground truth, compute per-tactic F1, flag worst."""
    gt_by_id = {gt.alert_id: gt for gt in ground_truths}

    per_tactic: dict[str, TacticMetrics] = {}
    failures_by_tactic: dict[str, list[str]] = {}

    # Track predicted-tactic counts for precision denominator
    predicted_tactic_total: dict[str, int] = {}
    predicted_tactic_correct: dict[str, int] = {}

    for pred in predictions:
        gt = gt_by_id.get(pred.alert_id)
        if gt is None:
            continue

        gt_tactic_key = gt.attack_tactic.value if gt.attack_tactic else "None"
        pred_tactic_key = pred.attack_tactic.value if pred.attack_tactic else "None"
        metrics = per_tactic.setdefault(gt_tactic_key, TacticMetrics())
        metrics.n_total += 1

        disposition_match = pred.disposition == gt.disposition
        tactic_match = (
            (pred.attack_tactic == gt.attack_tactic)
            if gt.attack_tactic is not None
            else pred.attack_tactic is None
        )
        correct = disposition_match and tactic_match

        predicted_tactic_total[pred_tactic_key] = (
            predicted_tactic_total.get(pred_tactic_key, 0) + 1
        )

        if correct:
            metrics.n_correct += 1
            predicted_tactic_correct[pred_tactic_key] = (
                predicted_tactic_correct.get(pred_tactic_key, 0) + 1
            )
        else:
            failures_by_tactic.setdefault(gt_tactic_key, []).append(str(pred.alert_id))

    for tactic_key, fail_ids in failures_by_tactic.items():
        per_tactic[tactic_key].failure_trace_ids = fail_ids

    for tactic_key, metrics in per_tactic.items():
        tp = metrics.n_correct
        # Recall: of all alerts truly in this tactic, how many did we get right?
        metrics.recall = tp / metrics.n_total if metrics.n_total else 0.0
        # Precision: of all alerts we *predicted* as this tactic, how many were right?
        pred_total = predicted_tactic_total.get(tactic_key, 0)
        pred_correct = predicted_tactic_correct.get(tactic_key, 0)
        metrics.precision = pred_correct / pred_total if pred_total else 0.0
        if metrics.precision + metrics.recall > 0:
            metrics.f1 = (
                2 * metrics.precision * metrics.recall
                / (metrics.precision + metrics.recall)
            )

    flagged: Tactic | None = None
    lowest_f1 = float("inf")
    for tactic_key, metrics in per_tactic.items():
        if metrics.n_total >= 5 and metrics.f1 < 0.6 and metrics.f1 < lowest_f1:
            lowest_f1 = metrics.f1
            flagged = Tactic(tactic_key) if tactic_key in [t.value for t in Tactic] else None

    return EvalResult(
        eval_run_id=eval_run_id,
        batch_size=len(predictions),
        per_tactic=per_tactic,
        flagged_tactic=flagged,
    )
