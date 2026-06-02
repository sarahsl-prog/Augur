"""Tests for the MCP-based improvement agent."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from augur.data.enums import Tactic
from augur.improvement_phoenix import (
    _build_trace_summary,
    _extract_trace_output,
    run_improvement_phoenix,
)
from augur.phoenix_mcp_client import PhoenixTrace


class TestBuildTraceSummary:
    def test_empty_list_returns_empty_string(self):
        assert _build_trace_summary([]) == ""

    def test_formats_cases_with_predicted_and_actual(self):
        traces = [
            {
                "trace_id": "t-1",
                "reasoning": "Thought it was FP",
                "predicted_disposition": "False Positive",
                "predicted_tactic": "None",
                "actual_disposition": "True Positive - Critical",
                "actual_tactic": "Lateral Movement",
            }
        ]
        result = _build_trace_summary(traces)
        assert "Case 1" in result
        assert "False Positive" in result
        assert "True Positive - Critical" in result
        assert "Lateral Movement" in result

    def test_caps_at_ten_cases(self):
        traces = [
            {"trace_id": f"t-{i}", "reasoning": f"reason {i}"}
            for i in range(20)
        ]
        result = _build_trace_summary(traces)
        assert "Case 10" in result
        assert "Case 11" not in result


class TestExtractTraceOutput:
    def test_returns_empty_dict_for_empty_spans(self):
        trace = PhoenixTrace(
            trace_id="t-1", project_name="augur", start_time="", end_time=None, spans=[]
        )
        assert _extract_trace_output(trace) == {}

    def test_extracts_fallback_attributes(self):
        trace = PhoenixTrace(
            trace_id="t-1",
            project_name="augur",
            start_time="",
            end_time=None,
            spans=[
                {
                    "attributes": {
                        "disposition": "False Positive",
                        "alert_id": "a-1",
                        "reasoning": "Looks benign",
                    }
                }
            ],
        )
        result = _extract_trace_output(trace)
        assert result["disposition"] == "False Positive"
        assert result["alert_id"] == "a-1"
        assert result["reasoning"] == "Looks benign"

    def test_extracts_from_llm_output_messages(self):
        output_json = json.dumps({"disposition": "Benign Positive", "reasoning": "Admin tool"})
        trace = PhoenixTrace(
            trace_id="t-1",
            project_name="augur",
            start_time="",
            end_time=None,
            spans=[
                {
                    "attributes": {
                        "llm.output_messages": [{"content": output_json}],
                    }
                }
            ],
        )
        result = _extract_trace_output(trace)
        assert result["disposition"] == "Benign Positive"


class TestRunImprovementPhoenix:
    @pytest.mark.asyncio
    async def test_rewrites_prompt_from_phoenix_traces(self):
        mock_store = MagicMock()
        mock_store.get_prompt.return_value = "old prompt"
        mock_store.get_current_version.return_value = 1

        fake_trace = PhoenixTrace(
            trace_id="t-fail",
            project_name="augur",
            start_time="",
            end_time=None,
            spans=[
                {
                    "attributes": {
                        "disposition": "False Positive",
                        "alert_id": "a-1",
                        "reasoning": "Bad classification",
                    }
                }
            ],
        )

        mock_mcp = AsyncMock()
        mock_mcp.get_trace_by_id = AsyncMock(return_value=fake_trace)
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        gemini_response = MagicMock()
        gemini_response.text = json.dumps({
            "revised_prompt": "better prompt",
            "reasoning": "added negative example",
        })
        mock_vertex = MagicMock()
        mock_vertex.aio.models.generate_content = AsyncMock(return_value=gemini_response)

        with (
            patch("augur.improvement_phoenix.PromptStore", return_value=mock_store),
            patch("augur.improvement_phoenix.PhoenixMCPClient", return_value=mock_mcp),
            patch("augur.improvement_phoenix.Client", return_value=mock_vertex),
        ):
            result = await run_improvement_phoenix(
                tactic=Tactic.LATERAL_MOVEMENT,
                failed_trace_ids=["t-fail"],
                ground_truth_map={
                    "a-1": {
                        "disposition": "True Positive - Critical",
                        "attack_tactic": "Lateral Movement",
                    }
                },
                eval_run_id="eval-1",
                phoenix_api_key="fake",
            )

        assert result == "better prompt"
        mock_store.write_version.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_missing_prompt(self):
        mock_store = MagicMock()
        mock_store.get_prompt.return_value = ""

        with patch("augur.improvement_phoenix.PromptStore", return_value=mock_store):
            with pytest.raises(RuntimeError, match="No current prompt"):
                await run_improvement_phoenix(
                    tactic=Tactic.LATERAL_MOVEMENT,
                    failed_trace_ids=[],
                    ground_truth_map={},
                    phoenix_api_key="fake",
                )

    @pytest.mark.asyncio
    async def test_raises_on_empty_gemini_response(self):
        mock_store = MagicMock()
        mock_store.get_prompt.return_value = "old prompt"
        mock_store.get_current_version.return_value = 1

        mock_mcp = AsyncMock()
        mock_mcp.__aenter__ = AsyncMock(return_value=mock_mcp)
        mock_mcp.__aexit__ = AsyncMock(return_value=False)

        gemini_response = MagicMock()
        gemini_response.text = ""
        mock_vertex = MagicMock()
        mock_vertex.aio.models.generate_content = AsyncMock(return_value=gemini_response)

        with (
            patch("augur.improvement_phoenix.PromptStore", return_value=mock_store),
            patch("augur.improvement_phoenix.PhoenixMCPClient", return_value=mock_mcp),
            patch("augur.improvement_phoenix.Client", return_value=mock_vertex),
        ):
            with pytest.raises(RuntimeError, match="empty response"):
                await run_improvement_phoenix(
                    tactic=Tactic.LATERAL_MOVEMENT,
                    failed_trace_ids=[],
                    ground_truth_map={},
                    eval_run_id="eval-2",
                    phoenix_api_key="fake",
                )
