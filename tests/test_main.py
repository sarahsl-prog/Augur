"""Tests for the FastAPI application surface."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from augur.data.enums import Disposition, Tactic
from augur.data.schema import Alert, TriageOutput, Severity
from augur.eval import EvalResult, TacticMetrics
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


@patch("augur.main._persist_eval")
@patch("augur.main.run_improvement", new_callable=AsyncMock)
@patch("augur.main.run_eval")
@patch("augur.main.run_triage", new_callable=AsyncMock)
def test_batch_legacy_path(mock_triage, mock_eval, mock_improve, _mock_persist):
    """Legacy /batch path: inline eval, no MCP."""
    from uuid import uuid4

    mock_triage.return_value = TriageOutput(
        alert_id=uuid4(),
        disposition=Disposition.TRUE_POSITIVE_CRITICAL,
        attack_tactic=Tactic.CREDENTIAL_ACCESS,
        attack_technique="T1110",
        confidence=0.9,
        severity=Severity.HIGH,
        reasoning="brute force",
        trace_id="trace-legacy-1",
    )
    mock_eval.return_value = EvalResult(
        eval_run_id="eval-legacy-1",
        batch_size=1,
        per_tactic={
            "Credential Access": TacticMetrics(
                n_total=5, n_correct=2, precision=0.4, recall=0.4, f1=0.4,
                failure_trace_ids=["f-1", "f-2"],
            ),
        },
        flagged_tactic=Tactic.CREDENTIAL_ACCESS,
    )

    response = client.post(
        "/batch",
        json={"n": 1, "eval_every": 1, "improve": True, "use_phoenix_mcp": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["triaged"] == 1
    assert data["eval_run_id"] == "eval-legacy-1"
    assert data["flagged_tactic"] == "Credential Access"
    assert data["mcp_enabled"] is False


@patch("augur.main._persist_eval")
@patch("augur.main.run_eval")
@patch("augur.main.run_triage", new_callable=AsyncMock)
def test_batch_legacy_no_flag(mock_triage, mock_eval, _mock_persist):
    """Legacy /batch when no tactic is flagged → improved=False."""
    from uuid import uuid4

    mock_triage.return_value = TriageOutput(
        alert_id=uuid4(),
        disposition=Disposition.FALSE_POSITIVE,
        confidence=0.8,
        severity=Severity.LOW,
        reasoning="noise",
        trace_id="trace-legacy-2",
    )
    mock_eval.return_value = EvalResult(
        eval_run_id="eval-legacy-2",
        batch_size=1,
        per_tactic={},
        flagged_tactic=None,
    )

    response = client.post(
        "/batch",
        json={"n": 1, "eval_every": 1, "improve": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["improved"] is False
    assert data["flagged_tactic"] is None
