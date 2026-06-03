"""Pub/Sub push ingestion handler.

Receives one alert per Pub/Sub push message, triages it via the ADK agent,
and persists the result to Firestore ``triage_results``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from uuid import uuid4

from google.cloud import firestore
from pydantic import BaseModel

from augur.agents.triage import build_triage_agent, run_triage
from augur.data.schema import Alert, GroundTruth, TriageOutput

logger = logging.getLogger(__name__)


# ── Pub/Sub envelope models ──────────────────────────────────────────


class PubSubMessage(BaseModel):
    data: str
    message_id: str | None = None
    messageId: str | None = None
    publish_time: str | None = None
    publishTime: str | None = None
    attributes: dict[str, str] | None = None


class PubSubEnvelope(BaseModel):
    message: PubSubMessage
    subscription: str | None = None


class IngestPayload(BaseModel):
    """Decoded alert payload.  Ground truth is optional (only for synthetic/CICIDS)."""

    alert: Alert
    ground_truth: GroundTruth | None = None


# ── Helpers ───────────────────────────────────────────────────────────


def _get_firestore(project: str = "augur-495810") -> firestore.Client:
    return firestore.Client(project=project)


# ── Core handler ─────────────────────────────────────────────────────


async def handle_ingest(envelope: PubSubEnvelope) -> dict:
    """Process one Pub/Sub push message: decode -> triage -> persist.

    Returns a dict with triage summary (caller returns 200 to ACK).
    """
    raw = base64.b64decode(envelope.message.data).decode("utf-8")
    payload = IngestPayload.model_validate_json(raw)

    # Rebuild the agent each time so it picks up prompt updates from the
    # improvement agent (build_triage_agent reads current prompt from Firestore).
    agent = build_triage_agent()
    triage_output: TriageOutput = await run_triage(agent, payload.alert)

    # Offload synchronous Firestore write to a thread so we don't block the
    # event loop while Pub/Sub push is waiting for our 200 ACK.
    await asyncio.to_thread(_persist_triage, payload, triage_output)

    return {
        "alert_id": str(payload.alert.alert_id),
        "disposition": triage_output.disposition.value,
        "trace_id": triage_output.trace_id,
    }


def _persist_triage(
    payload: IngestPayload,
    triage_output: TriageOutput,
    project: str = "augur-495810",
) -> str:
    """Write triage result to Firestore.  Returns the document ID."""
    db = _get_firestore(project)
    doc_id = str(uuid4())
    doc = db.collection("triage_results").document(doc_id)

    gt_dict = None
    if payload.ground_truth is not None:
        gt_dict = payload.ground_truth.model_dump(mode="json")

    doc.set(
        {
            "alert_id": str(payload.alert.alert_id),
            "alert_json": payload.alert.model_dump(mode="json"),
            "ground_truth": gt_dict,
            "triage_output": triage_output.model_dump(mode="json"),
            "trace_id": triage_output.trace_id,
            "ingested_at": firestore.SERVER_TIMESTAMP,
            "source": payload.alert.source,
            "eval_batch_id": None,
        }
    )
    return doc_id
