"""Tests for the legacy improvement agent."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from augur.data.enums import Tactic
from augur.improvement import run_improvement


def _mock_gemini_response(revised_prompt: str, reasoning: str) -> MagicMock:
    response = MagicMock()
    response.text = json.dumps({
        "revised_prompt": revised_prompt,
        "reasoning": reasoning,
    })
    return response


def _mock_empty_response() -> MagicMock:
    response = MagicMock()
    response.text = ""
    return response


class TestRunImprovement:
    @pytest.mark.asyncio
    async def test_rewrites_prompt_and_writes_to_store(self):
        mock_store = MagicMock()
        mock_store.get_prompt.return_value = "old prompt text"
        mock_store.get_current_version.return_value = 1

        response = _mock_gemini_response("new improved prompt", "added examples")
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=response)

        with (
            patch("augur.improvement.PromptStore", return_value=mock_store),
            patch("augur.improvement.Client", return_value=mock_client),
        ):
            result = await run_improvement(
                tactic=Tactic.LATERAL_MOVEMENT,
                failed_traces=[{"agent_reasoning": "wrong because..."}],
                eval_run_id="eval-1",
            )

        assert result == "new improved prompt"
        mock_store.write_version.assert_called_once()
        call_kwargs = mock_store.write_version.call_args.kwargs
        assert call_kwargs["tactic"] == Tactic.LATERAL_MOVEMENT
        assert call_kwargs["system_prompt"] == "new improved prompt"
        assert call_kwargs["created_by"] == "improvement_agent"

    @pytest.mark.asyncio
    async def test_raises_on_empty_gemini_response(self):
        mock_store = MagicMock()
        mock_store.get_prompt.return_value = "old prompt text"

        response = _mock_empty_response()
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=response)

        with (
            patch("augur.improvement.PromptStore", return_value=mock_store),
            patch("augur.improvement.Client", return_value=mock_client),
        ):
            with pytest.raises(RuntimeError, match="empty response"):
                await run_improvement(
                    tactic=Tactic.LATERAL_MOVEMENT,
                    failed_traces=[],
                    eval_run_id="eval-2",
                )

    @pytest.mark.asyncio
    async def test_raises_when_no_current_prompt(self):
        mock_store = MagicMock()
        mock_store.get_prompt.return_value = ""

        with patch("augur.improvement.PromptStore", return_value=mock_store):
            with pytest.raises(RuntimeError, match="No current prompt"):
                await run_improvement(
                    tactic=Tactic.LATERAL_MOVEMENT,
                    failed_traces=[],
                    eval_run_id="eval-3",
                )
