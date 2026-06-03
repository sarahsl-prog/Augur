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
        "ground_truth": (
            {
                "alert_id": alert_id,
                "disposition": disposition,
                "attack_tactic": tactic,
            }
            if tactic
            else None
        ),
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
        mock_query.stream.return_value = [_make_triage_doc()]
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value = (
            mock_query
        )
        mock_get_fs.return_value = mock_db

        req = EvalTriggerRequest(min_pending=5)
        result = await trigger_eval(req)

        assert result.status == "skipped"
        assert result.pending_count == 1
        assert result.eval_run_id is None

    @pytest.mark.asyncio
    @patch("augur.eval_trigger._persist_eval")
    @patch("augur.eval_trigger._claim_pending_docs")
    @patch("augur.eval_trigger.run_eval")
    @patch("augur.eval_trigger._get_firestore")
    async def test_runs_eval_when_enough_pending(
        self, mock_get_fs, mock_eval, mock_claim, mock_persist
    ):
        docs = [_make_triage_doc() for _ in range(10)]
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = docs
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value = (
            mock_query
        )
        mock_get_fs.return_value = mock_db
        mock_claim.return_value = [d.id for d in docs]

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
    @patch("augur.eval_trigger._claim_pending_docs")
    @patch("augur.eval_trigger.run_improvement", new_callable=AsyncMock)
    @patch("augur.eval_trigger.run_eval")
    @patch("augur.eval_trigger._get_firestore")
    async def test_triggers_improvement_on_flagged_tactic(
        self, mock_get_fs, mock_eval, mock_improve, mock_claim, mock_persist
    ):
        known_ids = [str(uuid4()) for _ in range(10)]
        docs = [_make_triage_doc(alert_id=aid) for aid in known_ids]
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.stream.return_value = docs
        mock_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value = (
            mock_query
        )
        mock_get_fs.return_value = mock_db
        mock_claim.return_value = [d.id for d in docs]

        mock_eval.return_value = EvalResult(
            eval_run_id="eval-trigger-2",
            batch_size=10,
            per_tactic={
                "Lateral Movement": TacticMetrics(
                    n_total=5,
                    n_correct=1,
                    precision=0.2,
                    recall=0.2,
                    f1=0.2,
                    failure_trace_ids=[known_ids[0], known_ids[1]],
                ),
            },
            flagged_tactic=Tactic.LATERAL_MOVEMENT,
        )

        req = EvalTriggerRequest(min_pending=5, improve=True)
        result = await trigger_eval(req)

        assert result.improved is True
        assert result.flagged_tactic == "Lateral Movement"
        mock_improve.assert_called_once()
