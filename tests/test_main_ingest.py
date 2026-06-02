"""Integration tests for /ingest and /eval/trigger endpoints via FastAPI TestClient."""

import base64
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
    def test_returns_200_on_valid_pubsub_message(self, mock_triage, mock_agent, mock_persist):
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

        response = client.post(
            "/ingest",
            json={
                "message": {"data": encoded, "message_id": "msg-test-1"},
                "subscription": "projects/augur-495810/subscriptions/alert-ingest-push",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["disposition"] == "False Positive"
        assert data["trace_id"] == "trace-ingest-ep"

    def test_returns_422_on_missing_message(self):
        response = client.post("/ingest", json={})
        assert response.status_code == 422


class TestEvalTriggerEndpoint:
    @patch("augur.eval_trigger._get_firestore")
    def test_skips_when_no_pending(self, mock_get_fs):
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = []
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value = (
            mock_query
        )
        mock_get_fs.return_value = mock_db

        response = client.post("/eval/trigger", json={"min_pending": 5})
        assert response.status_code == 200
        assert response.json()["status"] == "skipped"
