"""Tests for Pub/Sub push ingestion handler."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from augur.data.enums import Disposition, Tactic
from augur.data.schema import TriageOutput, Severity
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
        alerts, gts = generate_alert_batch(n=1)
        payload = IngestPayload(alert=alerts[0], ground_truth=gts[0])
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

        envelope = PubSubEnvelope(message=PubSubMessage(data=encoded))
        raw = base64.b64decode(envelope.message.data).decode()
        parsed = IngestPayload.model_validate_json(raw)
        assert parsed.ground_truth is None

    def test_camel_case_message_id_accepted(self):
        msg = PubSubMessage(data="dGVzdA==", messageId="msg-camel")
        assert msg.messageId == "msg-camel"


class TestHandleIngest:
    @pytest.mark.asyncio
    @patch("augur.ingest._persist_triage")
    @patch("augur.ingest.build_triage_agent")
    @patch("augur.ingest.run_triage", new_callable=AsyncMock)
    async def test_successful_ingest(self, mock_triage, mock_build, mock_persist):
        alerts, gts = generate_alert_batch(n=1)
        alert, gt = alerts[0], gts[0]

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
    @patch("augur.ingest.build_triage_agent")
    @patch("augur.ingest.run_triage", new_callable=AsyncMock)
    async def test_ingest_without_ground_truth(self, mock_triage, mock_build, mock_persist):
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
        envelope = PubSubEnvelope(message=PubSubMessage(data=encoded))

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
        envelope = PubSubEnvelope(message=PubSubMessage(data=encoded))
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
