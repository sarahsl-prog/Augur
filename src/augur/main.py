"""Augur FastAPI application — Cloud Run entry point.

Exposes /health, /, and /triage.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from augur.agents.triage import build_triage_agent, run_triage
from augur.data.schema import Alert, TriageOutput
from augur.tracing import init_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hook."""
    init_tracing()
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
    # TODO: inject trace_id from Phoenix current span (step 6 refinement)
    # For now, the trace exists in Phoenix even if the response doesn't echo trace_id
    return result
