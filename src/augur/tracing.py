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
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_initialized = False
_tracer: Any = None  # cached tracer after init


def _get_tracer() -> Any:
    """Return the OTel tracer if tracing is initialised, else None."""
    global _tracer
    if _tracer is not None:
        return _tracer
    try:
        from opentelemetry import trace as otel_trace
        _tracer = otel_trace.get_tracer("augur.triage")
        return _tracer
    except ImportError:
        return None


class _NoOpSpan:
    """Context-manager compatible when tracing is disabled."""

    trace_id = ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def set_attribute(self, *a, **k):
        pass

    def is_recording(self):
        return False


@contextmanager
def trace_span(span_name: str, **attributes: Any) -> Any:
    """Create a manual OpenTelemetry span with OpenInference attributes.

    Use inside ``run_triage`` (and any other non-ADK call site) so Phoenix
    Cloud still sees structured telemetry even when the direct Vertex API is
    used instead of the ADK runner.

    Yields a span that carries ``.trace_id`` as a hex string so callers can
    inject it into TriageOutput.
    """
    if os.environ.get("AUGUR_TRACING_DISABLED") == "1" or not _initialized:
        yield _NoOpSpan()
        return

    tracer = _get_tracer()
    if tracer is None:
        yield _NoOpSpan()
        return

    from opentelemetry import trace as otel_trace

    span = tracer.start_span(span_name, kind=otel_trace.SpanKind.INTERNAL)
    try:
        for k, v in attributes.items():
            # OpenInference uses string keys; numeric for booleans / numbers
            if isinstance(v, (bool, int, float)):
                span.set_attribute(str(k), v)
            else:
                span.set_attribute(str(k), str(v))
        # Expose trace_id so callers can inject it into responses
        span.trace_id = format(span.get_span_context().trace_id, "032x")
        yield span
    finally:
        span.end()


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
        logger.warning(
            "PHOENIX_API_KEY not set — tracing disabled. "
            "Set it from your Phoenix Cloud account, or set "
            "AUGUR_TRACING_DISABLED=1 to silence this warning."
        )
        _initialized = True
        return

    # Imports are inside the function so the module can be imported in
    # disabled-tracing contexts without pulling in the OTel stack.
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor
    from phoenix.otel import register

    tracer_provider = register(project_name=project_name, auto_instrument=True)


    ''' tracer_provider = register(
        project_name=project_name,
        endpoint="https://app.phoenix.arize.com/v1/traces",
        headers={"api_key": api_key},
        auto_instrument=False,
    ) '''

    GoogleADKInstrumentor().instrument(tracer_provider=tracer_provider)
    _initialized = True
    logger.info("Phoenix tracing initialized (project=%s)", project_name)
