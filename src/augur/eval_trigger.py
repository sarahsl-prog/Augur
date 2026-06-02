"""Scheduled eval trigger — reads accumulated triages from Firestore, evaluates.

Designed to be called by Cloud Scheduler every N minutes via POST /eval/trigger.
Collects un-evaluated triage_results, runs eval, and optionally triggers the
improvement agent when a tactic is flagged.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from google.cloud import firestore
from pydantic import BaseModel

from augur.data.enums import Tactic
from augur.data.schema import GroundTruth, TriageOutput
from augur.eval import run_eval
from augur.eval_phoenix import run_eval_phoenix
from augur.improvement import run_improvement
from augur.improvement_phoenix import run_improvement_phoenix
from augur.persistence import persist_eval as _persist_eval

logger = logging.getLogger(__name__)


class EvalTriggerRequest(BaseModel):
    use_phoenix_mcp: bool = False
    phoenix_api_key: str | None = None
    min_pending: int = 5
    improve: bool = True


class EvalTriggerResponse(BaseModel):
    status: str  # "skipped" | "evaluated"
    pending_count: int
    eval_run_id: str | None = None
    flagged_tactic: str | None = None
    improved: bool = False


def _get_firestore(project: str = "augur-495810") -> firestore.Client:
    return firestore.Client(project=project)


def _claim_pending_docs(db, doc_ids: list[str], batch_id: str) -> list[str]:
    """Atomically claim triage docs for an eval batch via a Firestore transaction.

    Returns the subset of doc_ids that were successfully claimed (i.e. still had
    eval_batch_id == None at read time).  This prevents two overlapping Scheduler
    invocations from double-evaluating the same documents.
    """

    @firestore.transactional
    def _txn(transaction):
        claimed = []
        for doc_id in doc_ids:
            ref = db.collection("triage_results").document(doc_id)
            snap = ref.get(transaction=transaction)
            if snap.exists and snap.to_dict().get("eval_batch_id") is None:
                transaction.update(ref, {"eval_batch_id": batch_id})
                claimed.append(doc_id)
        return claimed

    return _txn(db.transaction())


async def trigger_eval(req: EvalTriggerRequest) -> EvalTriggerResponse:
    """Collect un-evaluated triages from Firestore, run eval, optionally improve."""
    db = _get_firestore()

    query = (
        db.collection("triage_results")
        .where("eval_batch_id", "==", None)
        .order_by("ingested_at")
        .limit(200)
    )
    docs = list(query.stream())

    if len(docs) < req.min_pending:
        return EvalTriggerResponse(status="skipped", pending_count=len(docs))

    eval_run_id = str(uuid4())
    batch_id = str(uuid4())
    predictions: list[TriageOutput] = []
    ground_truths: list[GroundTruth] = []
    doc_ids: list[str] = []

    for doc in docs:
        data = doc.to_dict()
        doc_ids.append(doc.id)
        predictions.append(TriageOutput.model_validate(data["triage_output"]))
        if data.get("ground_truth"):
            ground_truths.append(GroundTruth.model_validate(data["ground_truth"]))

    claimed_ids = _claim_pending_docs(db, doc_ids, batch_id)

    if len(claimed_ids) < req.min_pending:
        return EvalTriggerResponse(status="skipped", pending_count=len(claimed_ids))

    doc_ids = claimed_ids

    if req.use_phoenix_mcp:
        eval_result = await run_eval_phoenix(
            ground_truths=ground_truths,
            eval_run_id=eval_run_id,
            project_name="augur",
            phoenix_api_key=req.phoenix_api_key,
        )
    else:
        eval_result = run_eval(
            predictions=predictions,
            ground_truths=ground_truths,
            eval_run_id=eval_run_id,
        )

    _persist_eval(eval_result)

    db.collection("eval_batches").document(batch_id).set(
        {
            "created_at": firestore.SERVER_TIMESTAMP,
            "status": "complete",
            "triage_doc_ids": doc_ids,
            "eval_run_id": eval_run_id,
            "improved": False,
        }
    )

    improved = False
    if req.improve and eval_result.flagged_tactic is not None:
        tactic = eval_result.flagged_tactic
        tactic_key = tactic.value if isinstance(tactic, Tactic) else tactic
        tactic_metrics = eval_result.per_tactic.get(tactic_key)

        if tactic_metrics and tactic_metrics.failure_trace_ids:
            if req.use_phoenix_mcp:
                gt_map = {
                    str(gt.alert_id): {
                        "disposition": gt.disposition.value if gt.disposition else None,
                        "attack_tactic": gt.attack_tactic.value if gt.attack_tactic else None,
                    }
                    for gt in ground_truths
                }
                await run_improvement_phoenix(
                    tactic=tactic,
                    failed_trace_ids=list(tactic_metrics.failure_trace_ids)[:10],
                    ground_truth_map=gt_map,
                    eval_run_id=eval_run_id,
                    phoenix_api_key=req.phoenix_api_key,
                )
            else:
                failed_ids = set(tactic_metrics.failure_trace_ids)
                failed_traces = [
                    {
                        "agent_reasoning": p.reasoning,
                        "disposition": p.disposition.value,
                        "alert_id": str(p.alert_id),
                    }
                    for p in predictions
                    if str(p.alert_id) in failed_ids
                ][:10]
                if failed_traces:
                    await run_improvement(
                        tactic=tactic,
                        failed_traces=failed_traces,
                        eval_run_id=eval_run_id,
                    )

            improved = True
            db.collection("eval_batches").document(batch_id).update({"improved": True})

    return EvalTriggerResponse(
        status="evaluated",
        pending_count=len(docs),
        eval_run_id=eval_run_id,
        flagged_tactic=(
            eval_result.flagged_tactic.value if eval_result.flagged_tactic else None
        ),
        improved=improved,
    )
