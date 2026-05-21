"""Augur FastAPI application — Cloud Run entry point.

Exposes /health, /, /triage, and /batch (closed-loop triage + optional eval + optional improvement).

The /batch endpoint now supports MCP-backed evaluation and improvement via
``use_phoenix_mcp``.  When enabled, the eval agent queries Phoenix Cloud
through the MCP server instead of comparing inline prediction dicts, and
the improvement agent fetches actual trace content from Phoenix rather than
the local failed-traces list.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from augur.agents.triage import build_triage_agent, run_triage
from augur.data.enums import Tactic
from augur.data.synthetic import generate_alert_batch
from augur.data.schema import Alert, TriageOutput, GroundTruth
from augur.eval import run_eval, TacticMetrics as _LegacyTacticMetrics
from augur.eval_phoenix import run_eval_phoenix, EvalResult as _McpEvalResult
from augur.improvement import run_improvement
from augur.improvement_phoenix import run_improvement_phoenix
from augur.prompt_store import PromptStore
from augur.tracing import init_tracing

from datetime import datetime, timezone
from google.cloud import firestore


def _persist_eval(eval_result, project="augur-495810"):
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
        "flagged_tactic": eval_result.flagged_tactic.value if eval_result.flagged_tactic else None,
    }
    # MCP-derived result carries extra Phoenix metadata
    if hasattr(eval_result, "trace_count"):
        payload["trace_count"] = eval_result.trace_count
    if hasattr(eval_result, "trace_ids"):
        payload["trace_ids"] = eval_result.trace_ids
    doc.set(payload)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hook."""
    init_tracing()
    # Seed Firestore with v1 prompts for every tactic (idempotent)
    try:
        from pathlib import Path
        prompt_path = Path(__file__).parents[2] / "prompts" / "triage_v1.md"
        base_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
        if base_prompt:
            PromptStore().seed_initial_prompts(base_prompt)
    except Exception:
        pass  # Don't fail startup if Firestore isn't reachable locally
    yield


app = FastAPI(title="Augur", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "augur", "version": "0.1.0"}


@app.post("/triage", response_model=TriageOutput)
async def triage(alert: Alert) -> TriageOutput:
    """Classify a single alert and return a structured triage report."""
    agent = build_triage_agent()
    result = await run_triage(agent, alert)
    return result


from pydantic import BaseModel


class BatchRequest(BaseModel):
    n: int = 25
    eval_every: int = 25
    improve: bool = True
    # NEW: when True, MCP-backed eval/improvement queries Phoenix directly
    use_phoenix_mcp: bool = False
    phoenix_api_key: str | None = None


class BatchResponse(BaseModel):
    triaged: int
    eval_run_id: str
    flagged_tactic: str | None
    improved: bool
    # NEW: conveys whether MCP was used so the response is self-describing
    mcp_enabled: bool = False


@app.post("/batch", response_model=BatchResponse)
async def batch(req: BatchRequest) -> BatchResponse:
    """Run a closed-loop batch: generate alerts, triage them, eval, optionally improve.

    If ``use_phoenix_mcp=True`` is provided, eval pulls triage predictions
    from Phoenix traces via the MCP server, and improvement fetches actual
    trace content from Phoenix instead of local failed-trace dicts.
    """
    import uuid

    alerts, ground_truths = generate_alert_batch(n=req.n)
    agent = build_triage_agent()
    outputs: list[TriageOutput] = []
    for alert in alerts:
        result = await run_triage(agent, alert)
        outputs.append(result)

    if req.use_phoenix_mcp:
        # ------------------------------------------------------------------
        # MCP path: eval + improvement query Phoenix Cloud via MCP
        # ------------------------------------------------------------------
        eval_result = await run_eval_phoenix(
            ground_truths=ground_truths,
            eval_run_id=str(uuid.uuid4()),
            project_name="augur",
            phoenix_api_key=req.phoenix_api_key,
        )
        _persist_eval(eval_result)

        improved = False
        if req.improve and eval_result.flagged_tactic is not None:
            tactic = eval_result.flagged_tactic
            tactic_metrics = eval_result.per_tactic.get(tactic.value)
            if tactic_metrics is not None and tactic_metrics.failure_trace_ids:
                # Build ground_truth_map keyed by alert_id (str)
                gt_map = {}
                for gt in ground_truths:
                    gt_map[str(gt.alert_id)] = {
                        "disposition": gt.disposition.value if gt.disposition else None,
                        "attack_tactic": gt.attack_tactic.value if gt.attack_tactic else None,
                        "attack_technique": gt.attack_technique or None,
                    }
                await run_improvement_phoenix(
                    tactic=tactic,
                    failed_trace_ids=list(tactic_metrics.failure_trace_ids)[:10],
                    ground_truth_map=gt_map,
                    eval_run_id=eval_result.eval_run_id,
                    phoenix_api_key=req.phoenix_api_key,
                )
                improved = True

        return BatchResponse(
            triaged=len(outputs),
            eval_run_id=eval_result.eval_run_id,
            flagged_tactic=eval_result.flagged_tactic.value if eval_result.flagged_tactic else None,
            improved=improved,
            mcp_enabled=True,
        )

    # ------------------------------------------------------------------
    # Legacy path: inline eval / improvement using local TriageOutput dicts
    # ------------------------------------------------------------------
    eval_result = run_eval(
        predictions=outputs,
        ground_truths=ground_truths,
        eval_run_id=str(uuid.uuid4()),
    )
    _persist_eval(eval_result)

    improved = False
    if req.improve and eval_result.flagged_tactic is not None:
        tactic = eval_result.flagged_tactic
        tactic_metrics = eval_result.per_tactic.get(tactic.value)
        if tactic_metrics is not None and tactic_metrics.failure_trace_ids:
            failed_ids = set(tactic_metrics.failure_trace_ids)
            failed_traces = [
                {"agent_reasoning": o.reasoning, "disposition": o.disposition.value, "alert_id": str(o.alert_id)}
                for o in outputs
                if str(o.alert_id) in failed_ids
            ][:10]
            if failed_traces:
                await run_improvement(
                    tactic=tactic,
                    failed_traces=failed_traces,
                    eval_run_id=eval_result.eval_run_id,
                )
                improved = True

    return BatchResponse(
        triaged=len(outputs),
        eval_run_id=eval_result.eval_run_id,
        flagged_tactic=eval_result.flagged_tactic.value if eval_result.flagged_tactic else None,
        improved=improved,
        mcp_enabled=False,
    )
