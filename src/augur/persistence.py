"""Firestore persistence helpers shared across modules."""

from __future__ import annotations

from datetime import datetime, timezone

from google.cloud import firestore


def persist_eval(eval_result, project: str = "augur-495810") -> None:
    """Write an EvalResult (legacy or MCP-derived) to Firestore eval_results collection."""
    db = firestore.Client(project=project)
    doc = db.collection("eval_results").document(eval_result.eval_run_id)
    per_tactic = {}
    for k, v in eval_result.per_tactic.items():
        per_tactic[k] = {
            "n_total": v.n_total,
            "n_correct": v.n_correct,
            "precision": v.precision,
            "recall": v.recall,
            "f1": v.f1,
            "accuracy": v.accuracy,
            "failure_trace_ids": v.failure_trace_ids,
        }
    payload = {
        "eval_run_id": eval_result.eval_run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "batch_size": eval_result.batch_size,
        "per_tactic": per_tactic,
        "flagged_tactic": (
            eval_result.flagged_tactic.value if eval_result.flagged_tactic else None
        ),
    }
    if hasattr(eval_result, "trace_count"):
        payload["trace_count"] = eval_result.trace_count
    if hasattr(eval_result, "trace_ids"):
        payload["trace_ids"] = eval_result.trace_ids
    doc.set(payload)
