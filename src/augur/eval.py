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
    # Pair by alert_id
    gt_by_id = {gt.alert_id: gt for gt in ground_truths}

    per_tactic: dict[str, TacticMetrics] = {}
    failures_by_tactic: dict[str, list[str]] = {}

    for pred in predictions:
        gt = gt_by_id.get(pred.alert_id)
        if gt is None:
            continue

        tactic_key = gt.attack_tactic.value if gt.attack_tactic else "None"
        metrics = per_tactic.setdefault(tactic_key, TacticMetrics())
        metrics.n_total += 1

        # "Correct" = disposition AND tactic match
        disposition_match = pred.disposition == gt.disposition
        tactic_match = (
            (pred.attack_tactic == gt.attack_tactic)
            if gt.attack_tactic is not None
            else pred.attack_tactic is None
        )
        if disposition_match and tactic_match:
            metrics.n_correct += 1
        else:
            failures_by_tactic.setdefault(tactic_key, []).append(str(pred.alert_id))

    # Fill failure lists
    for tactic_key, fail_ids in failures_by_tactic.items():
        per_tactic[tactic_key].failure_trace_ids = fail_ids

    # Compute F1 (treating "correct" as positive class, rest as negative)
    for metrics in per_tactic.values():
        tp = metrics.n_correct
        fp = metrics.n_total - metrics.n_correct
        # For recall, we need total relevant items. Since we only have items for this tactic,
        # we assume all items in the batch that belong to this tactic are relevant.
        # For precision: tp / (tp + fp) = tp / total
        # For recall: tp / total = same (since we only evaluate items where GT tactic = this tactic)
        # So precision = recall = accuracy in this context
        metrics.precision = tp / metrics.n_total if metrics.n_total else 0.0
        metrics.recall = tp / metrics.n_total if metrics.n_total else 0.0
        if metrics.precision + metrics.recall > 0:
            metrics.f1 = 2 * metrics.precision * metrics.recall / (metrics.precision + metrics.recall)

    # Flag the lowest-F1 tactic with >= 5 samples and F1 < 0.6
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
