"""Phoenix MCP-based improvement agent.

Queries Phoenix Cloud via the MCP server for failed-trace *content* (not just
IDs), then feeds that reasoning into a meta-prompt that rewrites the tactic's
system prompt.

**Key difference from the old improvement.py:**
- OLD:  receives a list of dicts from the /batch endpoint; never touches Phoenix.
- NEW:  receives trace_ids + tactic; fetches actual traces via MCP; extracts
        agent reasoning from span attributes; feeds *that* into the rewrite.
"""

from __future__ import annotations

import json as json_module
import logging
from typing import Any

from google.genai import Client
from google.genai.types import Content, Part, GenerateContentConfig

from augur.data.enums import Tactic
from augur.prompt_store import PromptStore
from augur.phoenix_mcp_client import PhoenixMCPClient

logger = logging.getLogger(__name__)

_IMPROVEMENT_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "revised_prompt": {"type": "STRING"},
        "reasoning": {"type": "STRING"},
    },
    "required": ["revised_prompt", "reasoning"],
}


def _build_trace_summary(traces: list[dict[str, Any]]) -> str:
    """Format failed-trace content for the meta-prompt.

    Each trace dict is expected to carry enough context for the LLM to
    understand what went wrong.  Keys: reasoning, predicted_disposition,
    predicted_tactic, actual_disposition, actual_tactic, trace_id, alert_id.
    """
    parts = []
    for i, t in enumerate(traces[:10]):  # cap at 10 examples
        reasoning = t.get("reasoning", "N/A")
        pred_disp = t.get("predicted_disposition", "N/A")
        actual_disp = t.get("actual_disposition", "N/A")
        pred_tactic = t.get("predicted_tactic", "N/A")
        actual_tactic = t.get("actual_tactic", "N/A")
        parts.append(
            f"""Case {i + 1} (trace {t.get('trace_id', 'N/A')}):
  Agent predicted: {pred_disp} / {pred_tactic}
  Ground truth:    {actual_disp} / {actual_tactic}
  Agent reasoning: {reasoning[:800]}
"""
        )
    return "\n".join(parts)


async def run_improvement_phoenix(
    tactic: Tactic,
    failed_trace_ids: list[str],
    ground_truth_map: dict[str, dict[str, Any]],
    eval_run_id: str = "",
    project: str = "augur-495810",
    location: str = "us-central1",
    phoenix_api_key: str | None = None,
) -> str:
    """Rewrite a tactic prompt using *actual Phoenix trace content* as negative examples.

    Args:
        tactic: The failing tactic.
        failed_trace_ids: Trace IDs of failures (from eval).
        ground_truth_map: Mapping alert_id -> gt dict with keys:
            ``disposition``, ``attack_tactic``, ``attack_technique``.
        eval_run_id: Identifier for the eval run that flagged this tactic.
        project: GCP project for Vertex.
        location: GCP region for Vertex.
        phoenix_api_key: Optional Phoenix API key.

    Returns:
        The new prompt text (written to Firestore as a new version).
    """
    store = PromptStore()
    current_prompt = store.get_prompt(tactic)
    if not current_prompt:
        raise RuntimeError(f"No current prompt found for tactic {tactic.value}")

    # ------------------------------------------------------------------
    # Phase 1 — Pull actual traces from Phoenix via MCP
    # ------------------------------------------------------------------
    failed_cases: list[dict[str, Any]] = []
    if failed_trace_ids:
        client = PhoenixMCPClient(api_key=phoenix_api_key)
        async with client:
            logger.info("Fetching %d failed traces from Phoenix MCP", len(failed_trace_ids))
            for trace_id in failed_trace_ids[:10]:
                trace = await client.get_trace_by_id(trace_id=trace_id)
                if trace is None:
                    continue
                # Try to recover model output from the trace
                parsed = _extract_trace_output(trace)
                alert_id = parsed.get("alert_id", "")
                gt = ground_truth_map.get(str(alert_id), {})
                failed_cases.append({
                    "trace_id": trace_id,
                    "alert_id": alert_id,
                    "reasoning": parsed.get("reasoning", trace.agent_reasoning),
                    "predicted_disposition": parsed.get("disposition", "N/A"),
                    "predicted_tactic": parsed.get("tactic", "N/A"),
                    "actual_disposition": gt.get("disposition", "N/A"),
                    "actual_tactic": gt.get("attack_tactic", "N/A"),
                })

    trace_summary = _build_trace_summary(failed_cases)
    if not trace_summary:
        # Fallback: no trace content available (e.g. MCP not reachable).
        logger.warning("No Phoenix trace content for improvement — using generic prompt.")
        trace_summary = "No detailed trace examples available.  Focus on general pitfalls."

    # ------------------------------------------------------------------
    # Phase 2 — Build meta-prompt and call Gemini for rewrite
    # ------------------------------------------------------------------
    meta_prompt = f"""You are a security-operations expert optimising classification prompts.

CURRENT PROMPT FOR {tactic.value}:
---
{current_prompt}
---

FAILED CASES FROM PHOENIX TRACES (agent got these wrong):
{trace_summary}

Your task: revise the CURRENT PROMPT so it would correctly classify the FAILED CASES above.
Keep the prompt concise (under 800 words). Preserve the JSON output format and enum values.
Do NOT change the enum definitions.

Respond with JSON only using these keys:
- revised_prompt: the full revised prompt text
- reasoning: 1-2 sentences on what changed and why
"""

    vertex_client = Client(vertexai=True, project=project, location=location)
    config = GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_IMPROVEMENT_RESPONSE_SCHEMA,
    )

    response = await vertex_client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=Content(
            role="user",
            parts=[Part(text=meta_prompt)],
        ),
        config=config,
    )

    raw = response.text.strip() if response.text else ""
    if not raw:
        raise RuntimeError("Improvement agent returned empty response")

    result = json_module.loads(raw)
    revised = result["revised_prompt"]

    # ------------------------------------------------------------------
    # Phase 3 — Persist new version to Firestore
    # ------------------------------------------------------------------
    store.write_version(
        tactic=tactic,
        system_prompt=revised,
        created_by="improvement_agent",
        parent_version=store.get_current_version(tactic),
        triggering_eval_id=eval_run_id,
    )

    return revised


def _extract_trace_output(trace) -> dict[str, Any]:
    """Best-effort extraction of model output from a Phoenix trace."""
    data = trace.spans[0].get("attributes", {}) if trace.spans else {}
    output: dict[str, Any] = {}
    if isinstance(data, dict):
        # OpenInference-instrumented spans store LLM I/O in attributes
        if "llm.output_messages" in data:
            msgs = data["llm.output_messages"]
            if isinstance(msgs, list) and msgs:
                content = msgs[0].get("content", "") if isinstance(msgs[0], dict) else str(msgs[0])
                try:
                    parsed = json_module.loads(content)
                    if isinstance(parsed, dict):
                        output = parsed
                except json_module.JSONDecodeError:
                    output["raw_output"] = content
        # Fallback attributes
        for key in ["disposition", "attack_tactic", "attack_technique", "reasoning", "alert_id"]:
            if key in data:
                output[key] = data[key]
    return output
