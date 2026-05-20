"""Minimal ADK agent used to verify Phoenix instrumentation end-to-end.

Delete this file after Task 15 lands — the real triage agent supersedes
it. Kept around through steps 1-5 because it's the smallest unit that
exercises ADK + OpenInference + Phoenix together.
"""

from google.adk.agents import Agent


def build_stub_agent() -> Agent:
    """Return an ADK agent that just echoes its input.

    Verify the import path against the canonical ADK docs if it doesn't
    resolve: see the augur-adk-patterns skill for the link.
    """
    return Agent(
        name="augur_stub",
        model="gemini-2.5-flash",
        description="Smoke-test agent — echoes input.",
        instruction="You are a smoke-test agent. Echo the user input verbatim.",
    )
