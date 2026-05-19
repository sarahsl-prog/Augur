"""Triage Agent — single hardcoded prompt (per-tactic prompts arrive at step 6).

Wraps the Google ADK to classify a single alert into a structured triage report,
with Phoenix tracing auto-captured by OpenInference.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, TriageOutput

logger = logging.getLogger(__name__)

# Load the v1 hardcoded prompt from the prompts/ directory
_PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "triage_v1.md"
_PROMPT_TEXT = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""


def build_triage_agent() -> Agent:
    """Return an ADK agent configured with the v1 hardcoded triage prompt.

    The agent receives a JSON-serialized Alert and must respond with JSON
    matching TriageOutput (minus trace_id, which is injected externally).
    """
    return Agent(
        name="augur_triage_v1",
        model="gemini-1.5-pro-002",
        description="Security alert triage — single hardcoded prompt.",
        instruction=_PROMPT_TEXT,
    )


def _parse_agent_response(raw: str) -> dict:
    """Extract JSON from an ADK agent response that may contain markdown."""
    text = raw.strip()
    # Strip markdown fences if present
    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()
    return json.loads(text)


async def run_triage(agent: Agent, alert: Alert) -> TriageOutput:
    """Run the triage agent against a single alert.

    Returns a validated TriageOutput. The caller is responsible for injecting
    trace_id from the current Phoenix span.
    """
    alert_json = alert.model_dump_json()

    # ADK runner setup — session service is in-memory for stateless triage
    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        session_service=session_service,
        app_name="augur",
        auto_create_session=True,
    )

    msg = Content(role="user", parts=[Part(text=alert_json)])

    response_text = ""
    async for event in runner.run_async(
        user_id="augur_triage",
        session_id=alert.alert_id.hex,
        new_message=msg,
    ):
        if (
            hasattr(event, "content")
            and event.content
            and event.content.parts
        ):
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    response_text = part.text

    if not response_text:
        raise RuntimeError("ADK agent returned no text response")

    data = _parse_agent_response(response_text)

    # Map the raw JSON into TriageOutput; alert_id is injected from the original alert
    output = TriageOutput(
        alert_id=alert.alert_id,
        disposition=Disposition(data["disposition"]),
        attack_tactic=Tactic(data["attack_tactic"]) if data.get("attack_tactic") else None,
        attack_technique=data.get("attack_technique"),
        attack_technique_name=data.get("attack_technique_name"),
        confidence=data.get("confidence", 0.5),
        severity=data["severity"],
        recommended_action=data.get("recommended_action", ""),
        reasoning=data.get("reasoning", ""),
        trace_id="",  # injected by caller from Phoenix span
    )
    return output
