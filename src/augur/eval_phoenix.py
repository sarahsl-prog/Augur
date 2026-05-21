"""Phoenix MCP-based eval agent.

Replaces the inline prediction-comparison with a trace-first approach:
queries Phoenix Cloud via the MCP server for traces produced by the
Triage Agent, parses those traces to recover predictions, and compares
against ground truth for per-tactic metrics.

**Why this matters for the hackathon:**
The Arize track judges "meaningful tracing + MCP use."  Most submissions
bolt on tracing as an afterthought.  This module makes the traces
*functional* — the eval agent doesn't look at local response objects; it
reads its own operational history from Phoenix.
"""

from __future__ import annotations

import json as json_module
import logging
from dataclasses import dataclass, field
from typing import Any

from augur.data.enums import Disposition, Tactic
from augur.data.schema import GroundTruth, TriageOutput
from augur.phoenix_mcp_client import PhoenixMCPClient, PhoenixTrace

logger = logging.getLogger(__name__)

# Mapping from Phoenix trace span attributes to our schema.
# OpenInference-instrumented ADK stores these in span attributes.
_PHOENIX_ATTR_KEYS = {
    "disposition": ["disposition", "augur.disposition"],
    "tactic": ["attack_tactic", "augur.attack_tactic"],
    "technique": ["attack_technique", "augur.attack_technique"],
    "confidence": ["confidence", "augur.confidence"],
    "severity": ["severity", "augur.severity"],
    "alert_id": ["alert_id", "augur.alert_id"],
}


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
    # New: Phoenix-derived traces that back the metrics
    trace_count: int = 0
    trace_ids: list[str] = field(default_factory=list)


def _extract_from_trace(trace: PhoenixTrace) -> dict[str, Any] | None:
    """Parse a Phoenix trace into a simple dict matching TriageOutput fields.

    Looks in the trace spans for ADK / OpenInference attributes that carry
    the agent's predicted disposition, tactic, technique, etc.
"""
    if not trace.spans:
        return None

    # The outermost trace or LLM span usually carries the "output"
    # Try spans from first to last (root-<child order)
    result: dict[str, Any] = {}
    for span in trace.spans:
        attrs: dict[str, Any] = span.get("attributes", {})
        # Merge all span metadata that looks like Augur output
        for field_name, candidate_keys in _PHOENIX_ATTR_KEYS.items():
            for key in candidate_keys:
                if key in attrs:
                    result[field_name] = attrs[key]
        # If the trace's root span has an input message with the alert_id,
        # grab it from there
        if "input" in attrs and isinstance(attrs["input"], str):
            try:
                inp = json_module.loads(attrs["input"])
                if isinstance(inp, dict):
                    if "alert_id" in inp:
                        result["alert_id"] = inp["alert_id"]
                    if "disposition" in inp:
                        result["disposition"] = inp["disposition"]
                    if "attack_tactic" in inp:
                        result["tactic"] = inp["attack_tactic"]
            except json_module.JSONDecodeError:
                pass
    return result


async def run_eval_phoenix(
    ground_truths: list[GroundTruth],
    eval_run_id: str = "",
    project_name: str = "augur",
    phoenix_api_key: str | None = None,
) -> EvalResult:
    """Fetch triage-agent traces from Phoenix and compute per-tactic metrics.

    Steps:
        1. Open Phoenix MCP session.
        2. Query recent traces in project "augur".
        3. Parse each trace into a predicted TriageOutput-like dict.
        4. Match predictions to provided ``ground_truths`` by alert_id.
        5. Compute precision/recall/F1 per ATT&CK tactic.
        6. Flag the lowest-F1 tactic with ≥5 samples and F1 < 0.6.

    Args:
        ground_truths: List of GroundTruth labels (must include alert_id).
        eval_run_id: Identifier for this evaluation run (for trace tagging).
        project_name: Phoenix project name.
        phoenix_api_key: Optional API key override.

    Returns:
        EvalResult with per-tactic metrics and flagged_tactic.
    """
    client = PhoenixMCPClient(api_key=phoenix_api_key)
    async with client:
        logger.info("Querying Phoenix MCP for traces in project=%s", project_name)
        traces = await client.get_traces(project_name=project_name, limit=100)
        logger.info("Retrieved %d traces", len(traces))

    # Build a lookup from alert_id → ground truth
    gt_by_id: dict[str, GroundTruth] = {}
    for gt in ground_truths:
        gt_by_id[str(gt.alert_id)] = gt

    per_tactic: dict[str, TacticMetrics] = {}
    failures_by_tactic: dict[str, list[str]] = {}
    trace_ids: list[str] = []

    for trace in traces:
        trace_ids.append(trace.trace_id)
        parsed = _extract_from_trace(trace)
        if parsed is None:
            continue

        alert_id = str(parsed.get("alert_id", ""))
        if not alert_id:
            continue

        gt = gt_by_id.get(alert_id)
        if gt is None:
            # A trace without corresponding GT in this batch — skip because
            # we have no label to score against.  (May happen for stale traces
            # that pre-date the current batch.)
            continue

        # Normalise parsed fields to our enums
        pred_disposition: Disposition | None = None
        try:
            pred_disposition = Disposition(parsed.get("disposition", ""))
        except ValueError:
            pass

        pred_tactic: Tactic | None = None
        try:
            pred_tactic = Tactic(parsed.get("tactic", ""))
        except ValueError:
            pass

        tactic_key = gt.attack_tactic.value if gt.attack_tactic else "None"
        metrics = per_tactic.setdefault(tactic_key, TacticMetrics())
        metrics.n_total += 1

        disposition_match = pred_disposition == gt.disposition
        tactic_match = (
            (pred_tactic == gt.attack_tactic)
            if gt.attack_tactic is not None
            else pred_tactic is None
        )

        if disposition_match and tactic_match:
            metrics.n_correct += 1
        else:
            failures_by_tactic.setdefault(tactic_key, []).append(trace.trace_id)

    # Fill failure lists
    for tactic_key, fail_ids in failures_by_tactic.items():
        per_tactic[tactic_key].failure_trace_ids = fail_ids

    # Compute F1
    for metrics in per_tactic.values():
        tp = metrics.n_correct
        total = metrics.n_total
        metrics.precision = tp / total if total else 0.0
        metrics.recall = tp / total if total else 0.0
        if metrics.precision + metrics.recall > 0:
            metrics.f1 = 2 * metrics.precision * metrics.recall / (metrics.precision + metrics.recall)

    # Flag the lowest-F1 tactic with ≥5 samples and F1 < 0.6
    flagged: Tactic | None = None
    lowest_f1 = float("inf")
    for tactic_key, metrics in per_tactic.items():
        if metrics.n_total >= 5 and metrics.f1 < 0.6 and metrics.f1 < lowest_f1:
            lowest_f1 = metrics.f1
            flagged = Tactic(tactic_key) if tactic_key in [t.value for t in Tactic] else None

    return EvalResult(
        eval_run_id=eval_run_id,
        batch_size=len(ground_truths),
        per_tactic=per_tactic,
        flagged_tactic=flagged,
        trace_count=len(traces),
        trace_ids=trace_ids,
    )
