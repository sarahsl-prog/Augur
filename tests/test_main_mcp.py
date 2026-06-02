"""Tests for the MCP-backed /batch endpoint in main.py."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from augur.data.enums import Disposition, Tactic
from augur.eval_phoenix import EvalResult as McpEvalResult, TacticMetrics
from augur.main import app

client = TestClient(app)


@patch("augur.main._persist_eval")
@patch("augur.main.run_improvement_phoenix", new_callable=AsyncMock)
@patch("augur.main.run_eval_phoenix", new_callable=AsyncMock)
@patch("augur.main.run_triage", new_callable=AsyncMock)
def test_batch_with_phoenix_mcp(mock_triage, mock_eval_phoenix, mock_improvement_phoenix, _mock_persist):
    """When use_phoenix_mcp=True, /batch calls MCP eval + improvement."""
    from uuid import uuid4
    from augur.data.schema import TriageOutput, Severity

    mock_triage.return_value = TriageOutput(
        alert_id=uuid4(),
        disposition=Disposition.TRUE_POSITIVE_CRITICAL,
        attack_tactic=Tactic.INITIAL_ACCESS,
        attack_technique="T1071",
        confidence=0.95,
        severity=Severity.HIGH,
        reasoning="Phishing",
        trace_id="trace-mcp-1",
    )

    mock_eval_phoenix.return_value = McpEvalResult(
        eval_run_id="eval-mcp-1",
        batch_size=1,
        per_tactic={
            "Initial Access": TacticMetrics(
                n_total=5,
                n_correct=1,
                precision=0.2,
                recall=0.2,
                f1=0.2,
                failure_trace_ids=["trace-mcp-2", "trace-mcp-3"],
            ),
        },
        flagged_tactic=Tactic.INITIAL_ACCESS,
        trace_count=5,
        trace_ids=["trace-mcp-1", "trace-mcp-2", "trace-mcp-3"],
    )

    response = client.post(
        "/batch",
        json={"n": 1, "eval_every": 1, "improve": True, "use_phoenix_mcp": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["triaged"] == 1
    assert data["eval_run_id"] == "eval-mcp-1"
    assert data["flagged_tactic"] == "Initial Access"
    assert data["improved"] is True
    assert data["mcp_enabled"] is True

    # Verify MCP eval called with project name override
    mock_eval_phoenix.assert_called_once()
    call_kwargs = mock_eval_phoenix.call_args.kwargs
    assert call_kwargs.get("project_name") == "augur"

    # Verify MCP improvement forwarded the failure trace IDs (capped at 10)
    mock_improvement_phoenix.assert_called_once()
    improve_kwargs = mock_improvement_phoenix.call_args.kwargs
    assert improve_kwargs.get("tactic") == Tactic.INITIAL_ACCESS
    assert improve_kwargs.get("failed_trace_ids") == ["trace-mcp-2", "trace-mcp-3"]


@patch("augur.main._persist_eval")
@patch("augur.main.run_eval_phoenix", new_callable=AsyncMock)
@patch("augur.main.run_triage", new_callable=AsyncMock)
def test_batch_phoenix_mcp_no_improve(mock_triage, mock_eval_phoenix, _mock_persist):
    """When improve=False, MCP eval runs but improvement is skipped."""
    from uuid import uuid4
    from augur.data.schema import TriageOutput, Severity

    mock_triage.return_value = TriageOutput(
        alert_id=uuid4(),
        disposition=Disposition.TRUE_POSITIVE_CRITICAL,
        attack_tactic=Tactic.INITIAL_ACCESS,
        confidence=0.95,
        severity=Severity.HIGH,
        reasoning="Phishing",
        trace_id="trace-mcp-1",
    )

    mock_eval_phoenix.return_value = McpEvalResult(
        eval_run_id="eval-mcp-2",
        batch_size=1,
        per_tactic={
            "Initial Access": TacticMetrics(
                n_total=5,
                n_correct=1,
                precision=0.2,
                recall=0.2,
                f1=0.2,
                failure_trace_ids=["trace-mcp-2"],
            ),
        },
        flagged_tactic=Tactic.INITIAL_ACCESS,
        trace_count=1,
        trace_ids=["trace-mcp-1"],
    )

    response = client.post(
        "/batch",
        json={"n": 1, "eval_every": 1, "improve": False, "use_phoenix_mcp": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["improved"] is False
    assert data["mcp_enabled"] is True
