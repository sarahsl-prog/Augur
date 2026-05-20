"""Triage Agent — single hardcoded prompt (per-tactic prompts arrive at step 6).

Wraps the Google ADK to classify a single alert into a structured triage report,
with Phoenix tracing auto-captured by OpenInference.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai.types import Content, Part, GenerateContentConfig
from google.genai import Client

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, TriageOutput

logger = logging.getLogger(__name__)

# Load the v1 hardcoded prompt from the prompts/ directory
_PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "triage_v1.md"
_PROMPT_TEXT = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""


class VertexGemini(Gemini):
    """Gemini model configured for Vertex AI backend (hackathon requirement)."""

    @property
    def api_client(self):
        """Override to force Vertex AI backend with project/location."""
        from google.genai import Client
        return Client(
            vertexai=True,
            project="augur-495810",
            location="us-central1",
        )

    @property
    def _live_api_client(self):
        from google.genai import Client
        return Client(
            vertexai=True,
            project="augur-495810",
            location="us-central1",
        )


def build_triage_agent() -> Agent:
    """Return an ADK agent configured with the current prompt from Firestore.

    Reads the prompt for tactic "Initial Access" as the v1 unified prompt.
    Falls back to the local triage_v1.md if Firestore is unreachable.
    """
    try:
        from augur.prompt_store import PromptStore
        prompt_text = PromptStore().get_prompt(Tactic.INITIAL_ACCESS)
    except Exception:
        prompt_text = ""
    if not prompt_text:
        prompt_text = _PROMPT_TEXT
    return Agent(
        name="augur_triage_v1",
        model=VertexGemini(model="gemini-2.5-flash"),
        description="Security alert triage — reads prompt from Firestore store.",
        instruction=prompt_text,
    )


# JSON schema that Gemini will be forced to emit (native JSON mode)
_TRIAGE_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "disposition": {"type": "STRING", "enum": ["True Positive - Critical", "True Positive - Policy Violation", "False Positive", "Benign Positive", "Needs Investigation"]},
        "attack_tactic": {"type": "STRING", "enum": ["Initial Access", "Credential Access", "Lateral Movement", "Exfiltration", "Command & Control", "Defense Evasion"]},
        "attack_technique": {"type": "STRING"},
        "attack_technique_name": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
        "severity": {"type": "STRING", "enum": ["Low", "Medium", "High", "Critical"]},
        "recommended_action": {"type": "STRING"},
        "reasoning": {"type": "STRING"},
    },
    "required": ["disposition", "severity", "confidence", "recommended_action", "reasoning"],
}


async def run_triage(agent: Agent, alert: Alert) -> TriageOutput:
    """Run the triage agent against a single alert using Gemini native JSON mode.

    Returns a validated TriageOutput. The caller is responsible for injecting
    trace_id from the current Phoenix span.
    """
    alert_json = alert.model_dump_json()

    client = Client(
        vertexai=True,
        project="augur-495810",
        location="us-central1",
    )

    config = GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_TRIAGE_RESPONSE_SCHEMA,
    )

    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash",
        contents=Content(
            role="user",
            parts=[Part(text=f"{_PROMPT_TEXT}\n\nAlert JSON:\n{alert_json}")],
        ),
        config=config,
    )

    raw_text = response.text.strip() if response.text else ""
    if not raw_text:
        raise RuntimeError("Gemini returned empty response")

    data = json.loads(raw_text)

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
        trace_id="",
    )
    return output


def _parse_agent_response(raw: str) -> dict:
    """Extract JSON from a response that may contain markdown or prose.

    Kept for test compatibility and fallback parsing.
    """
    text = raw.strip()
    if not text:
        raise ValueError("Agent returned empty response — no text to parse.")

    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"No JSON object found in response. Raw text:\n{text[:500]}")
