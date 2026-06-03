"""Tests for the mock alert feeder."""

from unittest.mock import MagicMock, patch

from augur.data.enums import Tactic
from augur.feeder import publish_one, run_feeder


class TestPublishOne:
    def test_publishes_to_topic(self):
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
        assert alert_id

    def test_random_tactic_when_none(self):
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
        assert mock_sleep.call_count == 2

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
