"""Tests for the FastAPI application surface."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, TriageOutput
from augur.main import app
from augur.data.synthetic import generate_alert_batch


client = TestClient(app)


def test_health_endpoint_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_endpoint_returns_service_name():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "augur"


@patch("augur.main.run_triage", new_callable=AsyncMock)
def test_triage_endpoint_returns_triage_output(mock_run):
    from uuid import uuid4

    mocked = TriageOutput(
        alert_id=uuid4(),
        disposition=Disposition.FALSE_POSITIVE,
        confidence=0.85,
        severity="Medium",
        reasoning="Test reasoning",
        trace_id="trace-123",
    )
    mock_run.return_value = mocked

    alert = generate_alert_batch(n=1)[0][0]
    response = client.post("/triage", json=alert.model_dump(mode="json"))
    assert response.status_code == 200
    data = response.json()
    assert data["disposition"] == "False Positive"
    assert data["trace_id"] == "trace-123"


@patch("augur.main.run_triage", new_callable=AsyncMock)
def test_triage_endpoint_with_tactic(mock_run):
    from uuid import uuid4

    mocked = TriageOutput(
        alert_id=uuid4(),
        disposition=Disposition.TRUE_POSITIVE_CRITICAL,
        attack_tactic=Tactic.LATERAL_MOVEMENT,
        attack_technique="T1021.002",
        confidence=0.92,
        severity="High",
        reasoning="SMB lateral movement detected",
        trace_id="trace-456",
    )
    mock_run.return_value = mocked

    alert = generate_alert_batch(n=1)[0][0]
    response = client.post("/triage", json=alert.model_dump(mode="json"))
    assert response.status_code == 200
    data = response.json()
    assert data["attack_tactic"] == "Lateral Movement"
