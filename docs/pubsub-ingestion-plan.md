# Pub/Sub Push Ingestion — Implementation Plan

## Overview

Replace Augur's current self-contained `/batch` loop (generate → triage → eval → improve in one request) with an event-driven architecture using **Cloud Pub/Sub Push** subscriptions. External sources publish alerts to a topic; Augur receives them via push delivery, triages on arrival, and evaluates on a schedule.

```
┌─────────────┐     ┌──────────────┐     ┌────────────────────────────┐
│ Mock Feeder │────▶│  Pub/Sub     │────▶│  Augur Cloud Run           │
│ (Cloud Run  │     │  topic:      │     │  POST /ingest              │
│  Job)       │     │  alert-ingest│     │  ├─ deserialize Alert      │
├─────────────┤     └──────────────┘     │  ├─ run_triage(alert)      │
│ Real IDS    │────▶                     │  ├─ write triage to FS     │
│ SIEM webhook│────▶                     │  └─ ACK (return 200)       │
└─────────────┘                          ├────────────────────────────┤
                                         │  Cloud Scheduler (5 min)   │
                                         │  POST /eval/trigger        │
                                         │  ├─ read pending triages   │
                                         │  ├─ run_eval or run_eval_  │
                                         │  │   phoenix               │
                                         │  ├─ if flagged: improve    │
                                         │  └─ mark batch evaluated   │
                                         └────────────────────────────┘
```

---

## 1. GCP Infrastructure Setup (Terraform / gcloud)

### 1a. Pub/Sub Topic & Push Subscription

```bash
# Create topic
gcloud pubsub topics create alert-ingest \
  --project=augur-495810

# Create push subscription targeting Augur's Cloud Run service
gcloud pubsub subscriptions create alert-ingest-push \
  --project=augur-495810 \
  --topic=alert-ingest \
  --push-endpoint=https://augur-runtime-<hash>-uc.a.run.app/ingest \
  --ack-deadline=60 \
  --push-auth-service-account=augur-pubsub-invoker@augur-495810.iam.gserviceaccount.com
```

**IAM requirements:**
- Service account `augur-pubsub-invoker` needs `roles/run.invoker` on the Augur Cloud Run service.
- The Cloud Run service must **not** require authentication from Pub/Sub (or use the OIDC token from push-auth-service-account).

### 1b. Cloud Scheduler Job (Eval Trigger)

```bash
gcloud scheduler jobs create http eval-trigger \
  --project=augur-495810 \
  --location=us-central1 \
  --schedule="*/5 * * * *" \
  --uri="https://augur-runtime-<hash>-uc.a.run.app/eval/trigger" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"use_phoenix_mcp": false}' \
  --oidc-service-account-email=augur-scheduler@augur-495810.iam.gserviceaccount.com
```

### 1c. Firestore Collections (New)

Two new collections for the ingestion pipeline:

```
triage_results/{doc_id}
  ├── alert_id: string (UUID)
  ├── alert_json: map          # original Alert for replay
  ├── ground_truth: map | null # present for synthetic/CICIDS, null for real
  ├── triage_output: map       # full TriageOutput
  ├── trace_id: string
  ├── ingested_at: timestamp
  ├── eval_batch_id: string | null   # set when included in an eval run
  └── source: string           # "synthetic" | "cicids2017" | "real"

eval_batches/{batch_id}
  ├── created_at: timestamp
  ├── status: string           # "pending" | "running" | "complete"
  ├── triage_doc_ids: [string] # references to triage_results docs
  ├── eval_run_id: string | null
  └── improved: bool
```

---

## 2. Code Changes — `src/augur/`

### 2a. New File: `src/augur/ingest.py`

Pub/Sub message handler: deserializes the push envelope, triages the alert, writes result to Firestore.

```python
"""Pub/Sub push ingestion handler."""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from google.cloud import firestore
from pydantic import BaseModel

from augur.agents.triage import build_triage_agent, run_triage
from augur.data.schema import Alert, GroundTruth, TriageOutput

logger = logging.getLogger(__name__)

# ── Pub/Sub envelope models ──────────────────────────────────────────

class PubSubMessage(BaseModel):
    data: str                     # base64-encoded JSON
    message_id: str | None = None
    messageId: str | None = None  # Pub/Sub may use camelCase
    publish_time: str | None = None
    publishTime: str | None = None
    attributes: dict[str, str] | None = None

class PubSubEnvelope(BaseModel):
    message: PubSubMessage
    subscription: str | None = None

class IngestPayload(BaseModel):
    """Decoded alert payload. Ground truth is optional (only for synthetic/CICIDS)."""
    alert: Alert
    ground_truth: GroundTruth | None = None


# ── Core handler ─────────────────────────────────────────────────────

_agent = None  # lazily cached triage agent

def _get_agent():
    global _agent
    if _agent is None:
        _agent = build_triage_agent()
    return _agent

def _get_firestore(project: str = "augur-495810") -> firestore.Client:
    return firestore.Client(project=project)


async def handle_ingest(envelope: PubSubEnvelope) -> dict:
    """Process one Pub/Sub push message: decode → triage → persist.

    Returns a dict with triage summary (caller returns 200 to ACK).
    """
    raw = base64.b64decode(envelope.message.data).decode("utf-8")
    payload = IngestPayload.model_validate_json(raw)

    agent = _get_agent()
    triage_output: TriageOutput = await run_triage(agent, payload.alert)

    _persist_triage(payload, triage_output)

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
    """Write triage result to Firestore. Returns the document ID."""
    db = _get_firestore(project)
    doc_id = str(uuid4())
    doc = db.collection("triage_results").document(doc_id)

    gt_dict = None
    if payload.ground_truth is not None:
        gt_dict = payload.ground_truth.model_dump(mode="json")

    doc.set({
        "alert_id": str(payload.alert.alert_id),
        "alert_json": payload.alert.model_dump(mode="json"),
        "ground_truth": gt_dict,
        "triage_output": triage_output.model_dump(mode="json"),
        "trace_id": triage_output.trace_id,
        "ingested_at": firestore.SERVER_TIMESTAMP,
        "source": payload.alert.source,
        "eval_batch_id": None,
    })
    return doc_id
```

**Key design decisions:**
- The Pub/Sub message `data` is base64-encoded JSON containing `{"alert": {...}, "ground_truth": {...} | null}`.
- Ground truth travels with the alert for synthetic/CICIDS sources (needed for eval). Real IDS alerts set `ground_truth: null`.
- Triage agent is lazily cached (one ADK agent per container instance).
- Returning HTTP 200 from `/ingest` ACKs the message. Any exception → 5xx → Pub/Sub retries.

### 2b. New File: `src/augur/eval_trigger.py`

Scheduled eval: reads un-evaluated triage results from Firestore, runs eval, optionally triggers improvement.

```python
"""Scheduled eval trigger — reads accumulated triages from Firestore, evaluates."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from google.cloud import firestore
from pydantic import BaseModel

from augur.data.enums import Tactic
from augur.data.schema import GroundTruth, TriageOutput
from augur.eval import run_eval
from augur.eval_phoenix import run_eval_phoenix
from augur.improvement import run_improvement
from augur.improvement_phoenix import run_improvement_phoenix
from augur.main import _persist_eval

logger = logging.getLogger(__name__)


class EvalTriggerRequest(BaseModel):
    use_phoenix_mcp: bool = False
    phoenix_api_key: str | None = None
    min_pending: int = 5          # minimum triages to justify an eval run
    improve: bool = True


class EvalTriggerResponse(BaseModel):
    status: str                   # "skipped" | "evaluated"
    pending_count: int
    eval_run_id: str | None = None
    flagged_tactic: str | None = None
    improved: bool = False


def _get_firestore(project: str = "augur-495810") -> firestore.Client:
    return firestore.Client(project=project)


async def trigger_eval(req: EvalTriggerRequest) -> EvalTriggerResponse:
    """Collect un-evaluated triages from Firestore, run eval, optionally improve."""
    db = _get_firestore()

    # 1. Query triage_results where eval_batch_id is null
    query = (
        db.collection("triage_results")
        .where("eval_batch_id", "==", None)
        .order_by("ingested_at")
        .limit(200)
    )
    docs = list(query.stream())

    if len(docs) < req.min_pending:
        return EvalTriggerResponse(
            status="skipped",
            pending_count=len(docs),
        )

    # 2. Reconstruct predictions + ground truths from stored data
    eval_run_id = str(uuid4())
    batch_id = str(uuid4())
    predictions: list[TriageOutput] = []
    ground_truths: list[GroundTruth] = []
    doc_ids: list[str] = []

    for doc in docs:
        data = doc.to_dict()
        doc_ids.append(doc.id)
        predictions.append(TriageOutput.model_validate(data["triage_output"]))
        if data.get("ground_truth"):
            ground_truths.append(GroundTruth.model_validate(data["ground_truth"]))

    # 3. Mark docs as claimed by this eval batch (prevent double-eval)
    batch = db.batch()
    for doc_id in doc_ids:
        batch.update(
            db.collection("triage_results").document(doc_id),
            {"eval_batch_id": batch_id},
        )
    batch.commit()

    # 4. Run eval
    if req.use_phoenix_mcp:
        eval_result = await run_eval_phoenix(
            ground_truths=ground_truths,
            eval_run_id=eval_run_id,
            project_name="augur",
            phoenix_api_key=req.phoenix_api_key,
        )
    else:
        eval_result = run_eval(
            predictions=predictions,
            ground_truths=ground_truths,
            eval_run_id=eval_run_id,
        )

    _persist_eval(eval_result)

    # 5. Record eval batch metadata
    db.collection("eval_batches").document(batch_id).set({
        "created_at": firestore.SERVER_TIMESTAMP,
        "status": "complete",
        "triage_doc_ids": doc_ids,
        "eval_run_id": eval_run_id,
        "improved": False,
    })

    # 6. Optionally improve
    improved = False
    if req.improve and eval_result.flagged_tactic is not None:
        tactic = eval_result.flagged_tactic
        tactic_metrics = eval_result.per_tactic.get(
            tactic.value if isinstance(tactic, Tactic) else tactic
        )
        if tactic_metrics and tactic_metrics.failure_trace_ids:
            if req.use_phoenix_mcp:
                gt_map = {}
                for gt in ground_truths:
                    gt_map[str(gt.alert_id)] = {
                        "disposition": gt.disposition.value if gt.disposition else None,
                        "attack_tactic": gt.attack_tactic.value if gt.attack_tactic else None,
                    }
                await run_improvement_phoenix(
                    tactic=tactic,
                    failed_trace_ids=list(tactic_metrics.failure_trace_ids)[:10],
                    ground_truth_map=gt_map,
                    eval_run_id=eval_run_id,
                    phoenix_api_key=req.phoenix_api_key,
                )
            else:
                failed_traces = []
                for pred in predictions:
                    if str(pred.alert_id) in set(tactic_metrics.failure_trace_ids):
                        failed_traces.append({
                            "agent_reasoning": pred.reasoning,
                            "disposition": pred.disposition.value,
                            "alert_id": str(pred.alert_id),
                        })
                if failed_traces:
                    await run_improvement(
                        tactic=tactic,
                        failed_traces=failed_traces[:10],
                        eval_run_id=eval_run_id,
                    )
            improved = True
            db.collection("eval_batches").document(batch_id).update({"improved": True})

    return EvalTriggerResponse(
        status="evaluated",
        pending_count=len(docs),
        eval_run_id=eval_run_id,
        flagged_tactic=(
            eval_result.flagged_tactic.value
            if eval_result.flagged_tactic else None
        ),
        improved=improved,
    )
```

### 2c. Modified: `src/augur/main.py`

Add two new endpoints. Keep all existing endpoints for backward compatibility.

```python
# ── NEW IMPORTS ──────────────────────────────────────────────────────
from augur.ingest import PubSubEnvelope, handle_ingest
from augur.eval_trigger import EvalTriggerRequest, EvalTriggerResponse, trigger_eval


# ── NEW ENDPOINTS ────────────────────────────────────────────────────

@app.post("/ingest")
async def ingest(envelope: PubSubEnvelope) -> dict:
    """Receive a Pub/Sub push message containing one alert.

    Pub/Sub considers HTTP 200-299 as ACK (message consumed).
    Any 4xx/5xx causes Pub/Sub to retry with exponential backoff.
    """
    result = await handle_ingest(envelope)
    return result


@app.post("/eval/trigger", response_model=EvalTriggerResponse)
async def eval_trigger(req: EvalTriggerRequest) -> EvalTriggerResponse:
    """Triggered by Cloud Scheduler every 5 minutes.

    Reads un-evaluated triage results from Firestore,
    runs eval, optionally triggers improvement.
    """
    result = await trigger_eval(req)
    return result
```

### 2d. New File: `src/augur/feeder.py`

Mock feeder script — runs as a Cloud Run Job. Generates and publishes one alert every 5-10 seconds.

```python
"""Mock alert feeder — publishes synthetic alerts to Pub/Sub.

Designed to run as a Cloud Run Job. Loops continuously:
  every 5-10 seconds, generates 1 alert and publishes to the
  ``alert-ingest`` topic.

Usage:
  # Local (requires GOOGLE_APPLICATION_CREDENTIALS or gcloud auth):
  python -m augur.feeder --topic alert-ingest --project augur-495810

  # Cloud Run Job:
  gcloud run jobs create augur-feeder \
    --image us-central1-docker.pkg.dev/augur-495810/augur/feeder:latest \
    --args="--topic=alert-ingest,--project=augur-495810,--count=0" \
    --region us-central1

  count=0 means infinite loop (default for Cloud Run Job).
  count=N means publish N alerts then exit (useful for demos).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time

from google.cloud import pubsub_v1

from augur.data.enums import Tactic
from augur.data.synthetic import generate_alert_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

TACTICS = list(Tactic)


def publish_one(
    publisher: pubsub_v1.PublisherClient,
    topic_path: str,
    tactic: Tactic | None = None,
) -> str:
    """Generate one alert + ground truth, publish to Pub/Sub. Returns alert_id."""
    if tactic is None:
        tactic = random.choice(TACTICS)

    # Generate a single alert for the chosen tactic
    from augur.data.enums import Disposition

    dispositions = [
        Disposition.TRUE_POSITIVE_CRITICAL,
        Disposition.TRUE_POSITIVE_POLICY,
        Disposition.FALSE_POSITIVE,
        Disposition.BENIGN_POSITIVE,
    ]
    disposition = random.choice(dispositions)

    from augur.data.synthetic import _make_alert
    alert, gt = _make_alert(tactic, disposition, random.randint(0, 9999))

    payload = {
        "alert": alert.model_dump(mode="json"),
        "ground_truth": gt.model_dump(mode="json"),
    }
    data = json.dumps(payload).encode("utf-8")

    future = publisher.publish(
        topic_path,
        data=data,
        source="mock-feeder",
        tactic=tactic.value,
    )
    message_id = future.result(timeout=30)
    logger.info(
        "Published alert %s | tactic=%s disposition=%s | msg_id=%s",
        alert.alert_id,
        tactic.value,
        disposition.value,
        message_id,
    )
    return str(alert.alert_id)


def run_feeder(
    project: str,
    topic: str,
    count: int = 0,
    min_delay: float = 5.0,
    max_delay: float = 10.0,
    tactic: str | None = None,
) -> None:
    """Main feeder loop.

    Args:
        project: GCP project ID.
        topic: Pub/Sub topic name (not full path).
        count: Number of alerts to publish. 0 = infinite.
        min_delay: Minimum seconds between publishes.
        max_delay: Maximum seconds between publishes.
        tactic: If set, only generate alerts for this ATT&CK tactic.
    """
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project, topic)

    fixed_tactic = None
    if tactic:
        fixed_tactic = Tactic(tactic)

    published = 0
    logger.info(
        "Feeder started — topic=%s count=%s tactic=%s delay=%.0f-%.0fs",
        topic_path,
        count or "infinite",
        fixed_tactic or "random",
        min_delay,
        max_delay,
    )

    try:
        while True:
            publish_one(publisher, topic_path, tactic=fixed_tactic)
            published += 1

            if count > 0 and published >= count:
                logger.info("Published %d alerts, exiting.", published)
                break

            delay = random.uniform(min_delay, max_delay)
            time.sleep(delay)
    except KeyboardInterrupt:
        logger.info("Feeder interrupted after %d alerts.", published)


def main():
    parser = argparse.ArgumentParser(description="Augur mock alert feeder")
    parser.add_argument("--project", default="augur-495810", help="GCP project ID")
    parser.add_argument("--topic", default="alert-ingest", help="Pub/Sub topic name")
    parser.add_argument(
        "--count", type=int, default=0,
        help="Number of alerts to publish (0 = infinite loop)",
    )
    parser.add_argument("--min-delay", type=float, default=5.0, help="Min seconds between alerts")
    parser.add_argument("--max-delay", type=float, default=10.0, help="Max seconds between alerts")
    parser.add_argument(
        "--tactic", default=None,
        help="Lock to one ATT&CK tactic (e.g. 'Lateral Movement')",
    )
    args = parser.parse_args()

    run_feeder(
        project=args.project,
        topic=args.topic,
        count=args.count,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        tactic=args.tactic,
    )


if __name__ == "__main__":
    main()
```

### 2e. New File: `Dockerfile.feeder`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY src/ ./src/
COPY prompts/ ./prompts/

ENTRYPOINT ["uv", "run", "python", "-m", "augur.feeder"]
```

### 2f. Modified: `pyproject.toml`

Add the Pub/Sub client dependency:

```diff
 dependencies = [
     ...
     "altair>=5.4.0",
+    "google-cloud-pubsub>=2.23.0",
 ]
```

### 2g. Modified: `cloudbuild.yaml`

Add the feeder image build step:

```yaml
  # --- Build feeder image ---
  - name: 'gcr.io/cloud-builders/docker'
    id: 'build-feeder'
    waitFor: ['-']
    args:
      - build
      - '-t'
      - 'us-central1-docker.pkg.dev/augur-495810/augur/feeder:latest'
      - '-f'
      - 'Dockerfile.feeder'
      - '.'

images:
  - 'us-central1-docker.pkg.dev/augur-495810/augur/runtime:latest'
  - 'us-central1-docker.pkg.dev/augur-495810/augur/dashboard:latest'
  - 'us-central1-docker.pkg.dev/augur-495810/augur/feeder:latest'
```

---

## 3. Dashboard Updates — `src/augur/dashboard/app.py`

The Alert Triage page currently uses synthetic demo data. Update it to read from the new `triage_results` Firestore collection:

```python
# In the Alert Triage page section, replace demo data generation with:

def _load_triage_results(db, limit=100):
    """Load recent triage results from Firestore."""
    docs = (
        db.collection("triage_results")
        .order_by("ingested_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    rows = []
    for doc in docs:
        data = doc.to_dict()
        triage = data.get("triage_output", {})
        rows.append({
            "alert_id": data.get("alert_id", ""),
            "source": data.get("source", "unknown"),
            "disposition": triage.get("disposition", ""),
            "attack_tactic": triage.get("attack_tactic", ""),
            "confidence": triage.get("confidence", 0),
            "severity": triage.get("severity", ""),
            "trace_id": triage.get("trace_id", ""),
            "ingested_at": data.get("ingested_at", ""),
            "eval_batch_id": data.get("eval_batch_id"),
        })
    return rows
```

Also add a new **Ingestion Status** sidebar indicator showing:
- Total triages pending eval
- Last eval run time
- Feeder status (publishing rate)

---

## 4. New Tests

### 4a. `tests/test_ingest.py` — Pub/Sub ingestion handler

```python
"""Tests for Pub/Sub push ingestion."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, GroundTruth, TriageOutput, Severity
from augur.data.synthetic import generate_alert_batch
from augur.ingest import (
    IngestPayload,
    PubSubEnvelope,
    PubSubMessage,
    handle_ingest,
    _persist_triage,
)


class TestPubSubEnvelopeDeserialization:
    def test_valid_envelope_with_alert_and_ground_truth(self):
        alert, gt = generate_alert_batch(n=1)
        payload = IngestPayload(alert=alert[0], ground_truth=gt[0])
        encoded = base64.b64encode(payload.model_dump_json().encode()).decode()

        envelope = PubSubEnvelope(
            message=PubSubMessage(data=encoded, message_id="msg-1"),
            subscription="projects/augur-495810/subscriptions/alert-ingest-push",
        )
        assert envelope.message.data == encoded

    def test_valid_envelope_without_ground_truth(self):
        alert = generate_alert_batch(n=1)[0][0]
        payload = IngestPayload(alert=alert, ground_truth=None)
        encoded = base64.b64encode(payload.model_dump_json().encode()).decode()

        envelope = PubSubEnvelope(
            message=PubSubMessage(data=encoded),
        )
        raw = base64.b64decode(envelope.message.data).decode()
        parsed = IngestPayload.model_validate_json(raw)
        assert parsed.ground_truth is None

    def test_camelCase_message_id_accepted(self):
        """Pub/Sub push may send messageId instead of message_id."""
        msg = PubSubMessage(data="dGVzdA==", messageId="msg-camel")
        assert msg.messageId == "msg-camel"


class TestHandleIngest:
    @pytest.mark.asyncio
    @patch("augur.ingest._persist_triage")
    @patch("augur.ingest._get_agent")
    @patch("augur.ingest.run_triage", new_callable=AsyncMock)
    async def test_successful_ingest(self, mock_triage, mock_agent, mock_persist):
        alert = generate_alert_batch(n=1)[0][0]
        gt = generate_alert_batch(n=1)[1][0]

        mock_triage.return_value = TriageOutput(
            alert_id=alert.alert_id,
            disposition=Disposition.FALSE_POSITIVE,
            confidence=0.9,
            severity=Severity.LOW,
            reasoning="test",
            trace_id="trace-ingest-1",
        )

        payload = IngestPayload(alert=alert, ground_truth=gt)
        encoded = base64.b64encode(payload.model_dump_json().encode()).decode()
        envelope = PubSubEnvelope(
            message=PubSubMessage(data=encoded, message_id="msg-1"),
        )

        result = await handle_ingest(envelope)

        assert result["alert_id"] == str(alert.alert_id)
        assert result["disposition"] == "False Positive"
        assert result["trace_id"] == "trace-ingest-1"
        mock_persist.assert_called_once()

    @pytest.mark.asyncio
    @patch("augur.ingest._persist_triage")
    @patch("augur.ingest._get_agent")
    @patch("augur.ingest.run_triage", new_callable=AsyncMock)
    async def test_ingest_without_ground_truth(self, mock_triage, mock_agent, mock_persist):
        alert = generate_alert_batch(n=1)[0][0]

        mock_triage.return_value = TriageOutput(
            alert_id=alert.alert_id,
            disposition=Disposition.TRUE_POSITIVE_CRITICAL,
            attack_tactic=Tactic.LATERAL_MOVEMENT,
            attack_technique="T1021.002",
            confidence=0.85,
            severity=Severity.HIGH,
            reasoning="SMB detected",
            trace_id="trace-ingest-2",
        )

        payload = IngestPayload(alert=alert, ground_truth=None)
        encoded = base64.b64encode(payload.model_dump_json().encode()).decode()
        envelope = PubSubEnvelope(
            message=PubSubMessage(data=encoded),
        )

        result = await handle_ingest(envelope)
        assert result["disposition"] == "True Positive - Critical"

    @pytest.mark.asyncio
    async def test_invalid_base64_raises(self):
        envelope = PubSubEnvelope(
            message=PubSubMessage(data="not-valid-base64!!!"),
        )
        with pytest.raises(Exception):
            await handle_ingest(envelope)

    @pytest.mark.asyncio
    async def test_invalid_json_payload_raises(self):
        encoded = base64.b64encode(b"not json").decode()
        envelope = PubSubEnvelope(
            message=PubSubMessage(data=encoded),
        )
        with pytest.raises(Exception):
            await handle_ingest(envelope)


class TestPersistTriage:
    @patch("augur.ingest._get_firestore")
    def test_writes_to_firestore(self, mock_get_fs):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc
        mock_get_fs.return_value = mock_db

        alert = generate_alert_batch(n=1)[0][0]
        gt = generate_alert_batch(n=1)[1][0]
        triage = TriageOutput(
            alert_id=alert.alert_id,
            disposition=Disposition.FALSE_POSITIVE,
            confidence=0.8,
            severity=Severity.LOW,
            reasoning="noise",
            trace_id="t-1",
        )

        payload = IngestPayload(alert=alert, ground_truth=gt)
        doc_id = _persist_triage(payload, triage)

        mock_doc.set.assert_called_once()
        call_data = mock_doc.set.call_args[0][0]
        assert call_data["alert_id"] == str(alert.alert_id)
        assert call_data["source"] == "synthetic"
        assert call_data["eval_batch_id"] is None

    @patch("augur.ingest._get_firestore")
    def test_persist_without_ground_truth(self, mock_get_fs):
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_db.collection.return_value.document.return_value = mock_doc
        mock_get_fs.return_value = mock_db

        alert = generate_alert_batch(n=1)[0][0]
        triage = TriageOutput(
            alert_id=alert.alert_id,
            disposition=Disposition.FALSE_POSITIVE,
            confidence=0.8,
            severity=Severity.LOW,
            reasoning="noise",
            trace_id="t-2",
        )

        payload = IngestPayload(alert=alert, ground_truth=None)
        _persist_triage(payload, triage)

        call_data = mock_doc.set.call_args[0][0]
        assert call_data["ground_truth"] is None
```

### 4b. `tests/test_eval_trigger.py` — Scheduled eval trigger

```python
"""Tests for scheduled eval trigger."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from augur.data.enums import Disposition, Tactic
from augur.eval import EvalResult, TacticMetrics
from augur.eval_trigger import EvalTriggerRequest, trigger_eval


def _make_triage_doc(alert_id=None, disposition="False Positive", tactic=None):
    """Build a mock Firestore document snapshot for triage_results."""
    alert_id = alert_id or str(uuid4())
    doc = MagicMock()
    doc.id = str(uuid4())
    doc.to_dict.return_value = {
        "alert_id": alert_id,
        "alert_json": {"alert_id": alert_id, "source": "synthetic"},
        "ground_truth": {
            "alert_id": alert_id,
            "disposition": disposition,
            "attack_tactic": tactic,
        } if tactic else None,
        "triage_output": {
            "alert_id": alert_id,
            "disposition": disposition,
            "confidence": 0.8,
            "severity": "Low",
            "reasoning": "test",
            "trace_id": f"trace-{alert_id[:8]}",
        },
        "trace_id": f"trace-{alert_id[:8]}",
        "eval_batch_id": None,
        "source": "synthetic",
    }
    return doc


class TestEvalTrigger:
    @pytest.mark.asyncio
    @patch("augur.eval_trigger._get_firestore")
    async def test_skips_when_below_min_pending(self, mock_get_fs):
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = [_make_triage_doc()]  # only 1 doc
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value = mock_query
        mock_get_fs.return_value = mock_db

        req = EvalTriggerRequest(min_pending=5)
        result = await trigger_eval(req)

        assert result.status == "skipped"
        assert result.pending_count == 1
        assert result.eval_run_id is None

    @pytest.mark.asyncio
    @patch("augur.eval_trigger._persist_eval")
    @patch("augur.eval_trigger.run_eval")
    @patch("augur.eval_trigger._get_firestore")
    async def test_runs_eval_when_enough_pending(self, mock_get_fs, mock_eval, mock_persist):
        docs = [_make_triage_doc() for _ in range(10)]
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = docs
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value = mock_query
        mock_get_fs.return_value = mock_db

        mock_eval.return_value = EvalResult(
            eval_run_id="eval-trigger-1",
            batch_size=10,
            per_tactic={},
            flagged_tactic=None,
        )

        req = EvalTriggerRequest(min_pending=5, improve=False)
        result = await trigger_eval(req)

        assert result.status == "evaluated"
        assert result.pending_count == 10
        mock_eval.assert_called_once()
        mock_persist.assert_called_once()

    @pytest.mark.asyncio
    @patch("augur.eval_trigger._persist_eval")
    @patch("augur.eval_trigger.run_improvement", new_callable=AsyncMock)
    @patch("augur.eval_trigger.run_eval")
    @patch("augur.eval_trigger._get_firestore")
    async def test_triggers_improvement_on_flagged_tactic(
        self, mock_get_fs, mock_eval, mock_improve, mock_persist
    ):
        docs = [_make_triage_doc() for _ in range(10)]
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = docs
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value = mock_query
        mock_get_fs.return_value = mock_db

        mock_eval.return_value = EvalResult(
            eval_run_id="eval-trigger-2",
            batch_size=10,
            per_tactic={
                "Lateral Movement": TacticMetrics(
                    n_total=5, n_correct=1, precision=0.2, recall=0.2, f1=0.2,
                    failure_trace_ids=["f-1", "f-2"],
                ),
            },
            flagged_tactic=Tactic.LATERAL_MOVEMENT,
        )

        req = EvalTriggerRequest(min_pending=5, improve=True)
        result = await trigger_eval(req)

        assert result.improved is True
        assert result.flagged_tactic == "Lateral Movement"
        mock_improve.assert_called_once()
```

### 4c. `tests/test_feeder.py` — Mock feeder

```python
"""Tests for the mock alert feeder."""

from unittest.mock import MagicMock, patch

from augur.data.enums import Tactic
from augur.feeder import publish_one, run_feeder


class TestPublishOne:
    @patch("augur.feeder.pubsub_v1.PublisherClient")
    def test_publishes_to_topic(self, _mock_cls):
        mock_publisher = MagicMock()
        mock_future = MagicMock()
        mock_future.result.return_value = "msg-123"
        mock_publisher.publish.return_value = mock_future

        alert_id = publish_one(
            mock_publisher,
            "projects/augur-495810/topics/alert-ingest",
            tactic=Tactic.LATERAL_MOVEMENT,
        )

        mock_publisher.publish.assert_called_once()
        call_args = mock_publisher.publish.call_args
        assert call_args[0][0] == "projects/augur-495810/topics/alert-ingest"
        assert call_args[1]["tactic"] == "Lateral Movement"
        assert alert_id  # non-empty string

    @patch("augur.feeder.pubsub_v1.PublisherClient")
    def test_random_tactic_when_none(self, _mock_cls):
        mock_publisher = MagicMock()
        mock_future = MagicMock()
        mock_future.result.return_value = "msg-456"
        mock_publisher.publish.return_value = mock_future

        alert_id = publish_one(
            mock_publisher,
            "projects/augur-495810/topics/alert-ingest",
            tactic=None,
        )
        assert alert_id
        mock_publisher.publish.assert_called_once()


class TestRunFeeder:
    @patch("augur.feeder.time.sleep")
    @patch("augur.feeder.publish_one")
    @patch("augur.feeder.pubsub_v1.PublisherClient")
    def test_publishes_n_alerts_then_exits(self, mock_cls, mock_publish, mock_sleep):
        mock_publish.return_value = "alert-1"

        run_feeder(
            project="augur-495810",
            topic="alert-ingest",
            count=3,
            min_delay=0.0,
            max_delay=0.0,
        )

        assert mock_publish.call_count == 3
        assert mock_sleep.call_count == 2  # sleeps between, not after last

    @patch("augur.feeder.time.sleep")
    @patch("augur.feeder.publish_one")
    @patch("augur.feeder.pubsub_v1.PublisherClient")
    def test_fixed_tactic(self, mock_cls, mock_publish, mock_sleep):
        mock_publish.return_value = "alert-1"

        run_feeder(
            project="augur-495810",
            topic="alert-ingest",
            count=1,
            tactic="Lateral Movement",
        )

        mock_publish.assert_called_once()
```

### 4d. `tests/test_main_ingest.py` — Integration test via FastAPI TestClient

```python
"""Integration tests for /ingest and /eval/trigger endpoints."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from augur.data.enums import Disposition, Tactic
from augur.data.schema import TriageOutput, Severity
from augur.data.synthetic import generate_alert_batch
from augur.ingest import IngestPayload
from augur.main import app

client = TestClient(app)


class TestIngestEndpoint:
    @patch("augur.ingest._persist_triage")
    @patch("augur.ingest._get_agent")
    @patch("augur.ingest.run_triage", new_callable=AsyncMock)
    def test_ingest_returns_200_on_valid_pubsub_message(
        self, mock_triage, mock_agent, mock_persist
    ):
        alert = generate_alert_batch(n=1)[0][0]
        gt = generate_alert_batch(n=1)[1][0]

        mock_triage.return_value = TriageOutput(
            alert_id=alert.alert_id,
            disposition=Disposition.FALSE_POSITIVE,
            confidence=0.9,
            severity=Severity.LOW,
            reasoning="test",
            trace_id="trace-ingest-ep",
        )

        payload = IngestPayload(alert=alert, ground_truth=gt)
        encoded = base64.b64encode(payload.model_dump_json().encode()).decode()

        response = client.post("/ingest", json={
            "message": {
                "data": encoded,
                "message_id": "msg-test-1",
            },
            "subscription": "projects/augur-495810/subscriptions/alert-ingest-push",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["disposition"] == "False Positive"
        assert data["trace_id"] == "trace-ingest-ep"

    def test_ingest_returns_422_on_missing_message(self):
        response = client.post("/ingest", json={})
        assert response.status_code == 422


class TestEvalTriggerEndpoint:
    @patch("augur.eval_trigger._get_firestore")
    def test_eval_trigger_skips_when_no_pending(self, mock_get_fs):
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = []
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value = mock_query
        mock_get_fs.return_value = mock_db

        response = client.post("/eval/trigger", json={"min_pending": 5})
        assert response.status_code == 200
        assert response.json()["status"] == "skipped"
```

---

## 5. File Change Summary

| File | Action | Description |
|------|--------|-------------|
| `src/augur/ingest.py` | **NEW** | Pub/Sub push handler: decode envelope → triage → persist to Firestore |
| `src/augur/eval_trigger.py` | **NEW** | Scheduled eval: read pending triages → eval → improve |
| `src/augur/feeder.py` | **NEW** | Mock feeder Cloud Run Job: publish alerts to Pub/Sub every 5-10s |
| `src/augur/main.py` | **MODIFY** | Add `POST /ingest` and `POST /eval/trigger` endpoints |
| `pyproject.toml` | **MODIFY** | Add `google-cloud-pubsub>=2.23.0` dependency |
| `cloudbuild.yaml` | **MODIFY** | Add feeder image build step |
| `Dockerfile.feeder` | **NEW** | Container image for the mock feeder Cloud Run Job |
| `src/augur/dashboard/app.py` | **MODIFY** | Read from `triage_results` collection instead of demo data |
| `tests/test_ingest.py` | **NEW** | 8 tests: envelope parsing, handle_ingest, persist |
| `tests/test_eval_trigger.py` | **NEW** | 3 tests: skip, eval, improve |
| `tests/test_feeder.py` | **NEW** | 4 tests: publish_one, run_feeder count/tactic |
| `tests/test_main_ingest.py` | **NEW** | 3 tests: FastAPI endpoint integration |

---

## 6. Deployment Steps (in order)

```bash
# 1. Create Pub/Sub topic
gcloud pubsub topics create alert-ingest --project=augur-495810

# 2. Deploy updated Augur runtime (with /ingest and /eval/trigger)
gcloud builds submit --config cloudbuild.yaml
gcloud run deploy augur-runtime \
  --image us-central1-docker.pkg.dev/augur-495810/augur/runtime:latest \
  --region us-central1 \
  --allow-unauthenticated  # or use IAM for prod

# 3. Get the Cloud Run URL
AUGUR_URL=$(gcloud run services describe augur-runtime \
  --region us-central1 --format='value(status.url)')

# 4. Create push subscription
gcloud pubsub subscriptions create alert-ingest-push \
  --topic=alert-ingest \
  --push-endpoint="${AUGUR_URL}/ingest" \
  --ack-deadline=60 \
  --push-auth-service-account=augur-pubsub-invoker@augur-495810.iam.gserviceaccount.com

# 5. Create Cloud Scheduler job for eval trigger
gcloud scheduler jobs create http eval-trigger \
  --location=us-central1 \
  --schedule="*/5 * * * *" \
  --uri="${AUGUR_URL}/eval/trigger" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"use_phoenix_mcp": false, "min_pending": 5, "improve": true}' \
  --oidc-service-account-email=augur-scheduler@augur-495810.iam.gserviceaccount.com

# 6. Deploy feeder as a Cloud Run Job
gcloud run jobs create augur-feeder \
  --image us-central1-docker.pkg.dev/augur-495810/augur/feeder:latest \
  --region us-central1 \
  --args="--topic=alert-ingest,--project=augur-495810,--count=0"

# 7. Start the feeder
gcloud run jobs execute augur-feeder --region us-central1
```

---

## 7. Backward Compatibility

- **`/batch` endpoint preserved**: existing batch workflow still works for demos and testing.
- **`/triage` endpoint preserved**: direct single-alert triage still works.
- **Feeder is optional**: without it, you can still publish to the topic manually or via curl:

```bash
# Manual publish for testing
gcloud pubsub topics publish alert-ingest \
  --message='{"alert": {...}, "ground_truth": {...}}'
```

---

## 8. Demo Flow (Updated)

1. Start feeder (Cloud Run Job) — alerts appear in dashboard every 5-10s
2. Dashboard shows real-time triage results flowing in from `/ingest`
3. Cloud Scheduler triggers eval every 5 minutes
4. When eval flags a tactic → improvement agent rewrites prompt
5. Subsequent alerts triaged with improved prompt → visible F1 improvement
6. Phoenix traces show the full pipeline for each alert
