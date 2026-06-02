"""Mock alert feeder — publishes synthetic alerts to Pub/Sub.

Designed to run as a Cloud Run Job.  Loops continuously, generating one
alert every 5-10 seconds and publishing it to the ``alert-ingest`` topic.

Usage (local)::

    python -m augur.feeder --topic alert-ingest --project augur-495810

Usage (Cloud Run Job)::

    gcloud run jobs create augur-feeder \
      --image us-central1-docker.pkg.dev/augur-495810/augur/feeder:latest \
      --args="--topic=alert-ingest,--project=augur-495810,--count=0" \
      --region us-central1

``count=0`` means infinite loop (default).
``count=N`` publishes N alerts then exits (useful for demos).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import time

from google.cloud import pubsub_v1

from augur.data.enums import Disposition, Tactic
from augur.data.synthetic import _make_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

_TACTICS = list(Tactic)
_DISPOSITIONS = [
    Disposition.TRUE_POSITIVE_CRITICAL,
    Disposition.TRUE_POSITIVE_POLICY,
    Disposition.FALSE_POSITIVE,
    Disposition.BENIGN_POSITIVE,
]


def publish_one(
    publisher: pubsub_v1.PublisherClient,
    topic_path: str,
    tactic: Tactic | None = None,
) -> str:
    """Generate one alert + ground truth, publish to Pub/Sub.  Returns alert_id."""
    if tactic is None:
        tactic = random.choice(_TACTICS)

    disposition = random.choice(_DISPOSITIONS)
    alert, gt = _make_alert(tactic, disposition, random.randint(0, 9999))

    payload = {
        "alert": alert.model_dump(mode="json"),
        "ground_truth": gt.model_dump(mode="json"),
    }
    data = json.dumps(payload).encode("utf-8")

    future = publisher.publish(
        topic_path,
        data=data,
        source="mock-feeder",
        tactic=tactic.value,
    )
    message_id = future.result(timeout=30)
    logger.info(
        "Published alert %s | tactic=%s disposition=%s | msg_id=%s",
        alert.alert_id,
        tactic.value,
        disposition.value,
        message_id,
    )
    return str(alert.alert_id)


def run_feeder(
    project: str,
    topic: str,
    count: int = 0,
    min_delay: float = 5.0,
    max_delay: float = 10.0,
    tactic: str | None = None,
) -> None:
    """Main feeder loop.

    Args:
        project: GCP project ID.
        topic: Pub/Sub topic name (not full path).
        count: Number of alerts to publish.  0 = infinite.
        min_delay: Minimum seconds between publishes.
        max_delay: Maximum seconds between publishes.
        tactic: If set, only generate alerts for this ATT&CK tactic.
    """
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project, topic)

    fixed_tactic = Tactic(tactic) if tactic else None

    published = 0
    logger.info(
        "Feeder started — topic=%s count=%s tactic=%s delay=%.0f-%.0fs",
        topic_path,
        count or "infinite",
        fixed_tactic or "random",
        min_delay,
        max_delay,
    )

    try:
        while True:
            publish_one(publisher, topic_path, tactic=fixed_tactic)
            published += 1

            if count > 0 and published >= count:
                logger.info("Published %d alerts, exiting.", published)
                break

            delay = random.uniform(min_delay, max_delay)
            time.sleep(delay)
    except KeyboardInterrupt:
        logger.info("Feeder interrupted after %d alerts.", published)


def main():
    parser = argparse.ArgumentParser(description="Augur mock alert feeder")
    parser.add_argument("--project", default="augur-495810")
    parser.add_argument("--topic", default="alert-ingest")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--min-delay", type=float, default=5.0)
    parser.add_argument("--max-delay", type=float, default=10.0)
    parser.add_argument("--tactic", default=None)
    args = parser.parse_args()

    run_feeder(
        project=args.project,
        topic=args.topic,
        count=args.count,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        tactic=args.tactic,
    )


if __name__ == "__main__":
    main()
