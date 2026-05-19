"""Augur FastAPI application — Cloud Run entry point.

Exposes /health for liveness checks and / for service identification.
The triage endpoint is added in Task 16.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

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
