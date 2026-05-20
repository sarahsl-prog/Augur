"""Improvement Agent — rewrite per-tactic prompt using failed traces as context."""

from __future__ import annotations

import json
import os
from typing import Optional

from google.genai import Client
from google.genai.types import Content, Part, GenerateContentConfig

from augur.data.enums import Tactic
from augur.prompt_store import PromptStore

# Response schema for the improvement agent
_IMPROVEMENT_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "revised_prompt": {"type": "STRING"},
        "reasoning": {"type": "STRING"},
    },
    "required": ["revised_prompt", "reasoning"],
}


async def run_improvement(
    tactic: Tactic,
    failed_traces: list[dict],
    eval_run_id: str = "",
    project: str = "augur-495810",
    location: str = "us-central1",
) -> str:
    """Rewrite the prompt for a tactic using failure cases.

    Returns the new prompt text (caller writes to Firestore).
    """
    store = PromptStore()
    current_prompt = store.get_prompt(tactic)

    if not current_prompt:
        raise RuntimeError(f"No current prompt found for tactic {tactic.value}")

    # Build meta-prompt
    trace_summary = "\n\n".join(
        f"Case {i+1}:\n{t.get('agent_reasoning', 'N/A')[:500]}"
        for i, t in enumerate(failed_traces[:10])
    )

    meta_prompt = f"""You are a security operations expert optimizing classification prompts.

CURRENT PROMPT FOR {tactic.value}:
---
{current_prompt}
---

FAILED CASES (agent got these wrong):
{trace_summary}

Your task: revise the CURRENT PROMPT so it would correctly classify the FAILED CASES above.
Keep the prompt concise (under 800 words). Preserve the JSON output format and enum values.
Respond with JSON only using these keys:
- revised_prompt: the full revised prompt text
- reasoning: 1-2 sentences on what changed and why
"""

    client = Client(vertexai=True, project=project, location=location)
    config = GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_IMPROVEMENT_RESPONSE_SCHEMA,
    )

    response = await client.aio.models.generate_content(
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

    result = json.loads(raw)
    revised = result["revised_prompt"]

    # Write to Firestore
    store.write_version(
        tactic=tactic,
        system_prompt=revised,
        created_by="improvement_agent",
        parent_version=store.get_current_version(tactic),
        triggering_eval_id=eval_run_id,
    )

    return revised
