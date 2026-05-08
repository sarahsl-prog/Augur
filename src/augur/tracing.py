"""Phoenix Cloud + OpenInference tracing setup for Augur.

Call ``init_tracing()`` exactly once at process startup, before any ADK
agent runs. Auto-instrumentation captures every LLM call and tool span;
no manual span management is required for the triage agent.

Disabled when AUGUR_TRACING_DISABLED=1 (set by tests/conftest.py for
unit tests). The Task 6 smoke test runs in a subprocess that doesn't
inherit the disable flag.
"""

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def init_tracing(project_name: str = "augur") -> None:
    """Register Phoenix Cloud + auto-instrument ADK. Idempotent."""
    global _initialized
    if _initialized:
        return
    if os.environ.get("AUGUR_TRACING_DISABLED") == "1":
        logger.info("Tracing disabled via AUGUR_TRACING_DISABLED — skipping init")
        _initialized = True
        return

    api_key = os.environ.get("PHOENIX_API_KEY")
    if not api_key:
        raise RuntimeError(
            "PHOENIX_API_KEY env var is required for tracing. "
            "Set it from your Phoenix Cloud account, or set "
            "AUGUR_TRACING_DISABLED=1 to skip tracing entirely."
        )

    # Imports are inside the function so the module can be imported in
    # disabled-tracing contexts without pulling in the OTel stack.
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor
    from phoenix.otel import register

    tracer_provider = register(
        project_name=project_name,
        endpoint="https://app.phoenix.arize.com/v1/traces",
        headers={"api_key": api_key},
        auto_instrument=True,
    )
    GoogleADKInstrumentor().instrument(tracer_provider=tracer_provider)
    _initialized = True
    logger.info("Phoenix tracing initialized (project=%s)", project_name)
