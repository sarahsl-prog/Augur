"""Augur FastAPI application — Cloud Run entry point.

Exposes /health for liveness checks and / for service identification.
The triage endpoint is added in Task 16.
"""

from fastapi import FastAPI

app = FastAPI(title="Augur", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "augur", "version": "0.1.0"}
