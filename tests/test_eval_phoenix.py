"""Tests for the MCP-based eval agent (run_eval_phoenix)."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from augur.data.enums import Disposition, Tactic
from augur.data.schema import GroundTruth
from augur.eval_phoenix import (
    EvalResult,
    TacticMetrics,
    _extract_from_trace,
    run_eval_phoenix,
)
from augur.phoenix_mcp_client import PhoenixTrace


class TestExtractFromTrace:
    def test_empty_spans_returns_none(self):
        trace = PhoenixTrace(
            trace_id="t-1", project_name="augur", start_time="", end_time=None, spans=[]
        )
        assert _extract_from_trace(trace) is None

    def test_extracts_augur_attributes(self):
        trace = PhoenixTrace(
            trace_id="t-1",
            project_name="augur",
            start_time="",
            end_time=None,
            spans=[
                {
                    "attributes": {
                        "augur.disposition": "True Positive - Critical",
                        "augur.attack_tactic": "Lateral Movement",
                        "augur.alert_id": "abc-123",
                    }
                }
            ],
        )
        result = _extract_from_trace(trace)
        assert result["disposition"] == "True Positive - Critical"
        assert result["tactic"] == "Lateral Movement"
        assert result["alert_id"] == "abc-123"

    def test_extracts_from_plain_attribute_keys(self):
        trace = PhoenixTrace(
            trace_id="t-1",
            project_name="augur",
            start_time="",
            end_time=None,
            spans=[
                {
                    "attributes": {
                        "disposition": "False Positive",
                        "alert_id": "def-456",
                    }
                }
            ],
        )
        result = _extract_from_trace(trace)
        assert result["disposition"] == "False Positive"
        assert result["alert_id"] == "def-456"

    def test_extracts_alert_id_from_input_json(self):
        import json

        trace = PhoenixTrace(
            trace_id="t-1",
            project_name="augur",
            start_time="",
            end_time=None,
            spans=[
                {
                    "attributes": {
                        "input": json.dumps({"alert_id": "from-input-789"}),
                    }
                }
            ],
        )
        result = _extract_from_trace(trace)
        assert result["alert_id"] == "from-input-789"

    def test_invalid_json_input_is_ignored(self):
        trace = PhoenixTrace(
            trace_id="t-1",
            project_name="augur",
            start_time="",
            end_time=None,
            spans=[{"attributes": {"input": "not-json", "alert_id": "ok"}}],
        )
        result = _extract_from_trace(trace)
        assert result["alert_id"] == "ok"


def _make_trace(alert_id: str, disposition: str, tactic: str) -> PhoenixTrace:
    return PhoenixTrace(
        trace_id=f"t-{alert_id}",
        project_name="augur",
        start_time="2026-01-01T00:00:00Z",
        end_time=None,
        spans=[
            {
                "attributes": {
                    "augur.disposition": disposition,
                    "augur.attack_tactic": tactic,
                    "augur.alert_id": alert_id,
                }
            }
        ],
    )


class TestRunEvalPhoenix:
    @pytest.mark.asyncio
    async def test_all_correct(self):
        aid = str(uuid4())
        traces = [_make_trace(aid, "True Positive - Critical", "Credential Access")]
        gts = [
            GroundTruth(
                alert_id=aid,
                disposition=Disposition.TRUE_POSITIVE_CRITICAL,
                attack_tactic=Tactic.CREDENTIAL_ACCESS,
                attack_technique="T1110",
            )
        ]

        with patch("augur.eval_phoenix.PhoenixMCPClient") as MockClient:
            instance = AsyncMock()
            instance.get_traces = AsyncMock(return_value=traces)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await run_eval_phoenix(
                ground_truths=gts,
                eval_run_id="e-mcp-1",
                phoenix_api_key="fake",
            )

        metrics = result.per_tactic.get("Credential Access")
        assert metrics is not None
        assert metrics.n_correct == 1
        assert metrics.recall == 1.0

    @pytest.mark.asyncio
    async def test_empty_traces_returns_empty_metrics(self):
        gts = [
            GroundTruth(
                alert_id=uuid4(),
                disposition=Disposition.TRUE_POSITIVE_CRITICAL,
                attack_tactic=Tactic.LATERAL_MOVEMENT,
            )
        ]

        with patch("augur.eval_phoenix.PhoenixMCPClient") as MockClient:
            instance = AsyncMock()
            instance.get_traces = AsyncMock(return_value=[])
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await run_eval_phoenix(
                ground_truths=gts,
                eval_run_id="e-mcp-2",
                phoenix_api_key="fake",
            )

        assert result.per_tactic == {}
        assert result.flagged_tactic is None

    @pytest.mark.asyncio
    async def test_mismatched_alert_id_skipped(self):
        traces = [_make_trace("no-match", "False Positive", "None")]
        gts = [
            GroundTruth(
                alert_id=uuid4(),
                disposition=Disposition.FALSE_POSITIVE,
            )
        ]

        with patch("augur.eval_phoenix.PhoenixMCPClient") as MockClient:
            instance = AsyncMock()
            instance.get_traces = AsyncMock(return_value=traces)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await run_eval_phoenix(
                ground_truths=gts,
                eval_run_id="e-mcp-3",
                phoenix_api_key="fake",
            )

        assert result.per_tactic == {}
