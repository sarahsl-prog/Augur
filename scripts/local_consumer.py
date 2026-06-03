"""Local development pull consumer.

Pulls messages from the ``alert-ingest-local`` Pub/Sub subscription and
processes them through the same ``handle_ingest`` coroutine used in production.

Usage::

    python scripts/local_consumer.py
    python scripts/local_consumer.py --project augur-495810 --subscription alert-ingest-local
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging

from google.cloud import pubsub_v1

from dotenv import load_dotenv
load_dotenv()

from augur.ingest import PubSubEnvelope, PubSubMessage, handle_ingest
from augur.tracing import init_tracing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def process_message(message: pubsub_v1.types.ReceivedMessage) -> bool:
    """Decode a pulled message and call handle_ingest.  Returns True on success."""
    pm = message.message
    envelope = PubSubEnvelope(
        message=PubSubMessage(
            data=base64.b64encode(pm.data).decode("utf-8"),
            message_id=pm.message_id,
            publish_time=str(pm.publish_time),
            attributes=dict(pm.attributes),
        )
    )
    try:
        result = await handle_ingest(envelope)
        logger.info("OK alert_id=%s disposition=%s", result["alert_id"], result["disposition"])
        return True
    except Exception:
        logger.exception("handle_ingest failed — message will be nacked and redelivered")
        return False


async def run(project: str, subscription: str, max_messages: int) -> None:
    sub_path = f"projects/{project}/subscriptions/{subscription}"
    subscriber = pubsub_v1.SubscriberClient()

    logger.info("Pulling from %s (max_messages=%d per batch)", sub_path, max_messages)

    with subscriber:
        while True:
            response = subscriber.pull(
                request={"subscription": sub_path, "max_messages": max_messages},
                timeout=10,
            )

            if not response.received_messages:
                logger.debug("No messages, waiting...")
                await asyncio.sleep(2)
                continue

            ack_ids, nack_ids = [], []
            for received in response.received_messages:
                ok = await process_message(received)
                (ack_ids if ok else nack_ids).append(received.ack_id)

            if ack_ids:
                subscriber.acknowledge(request={"subscription": sub_path, "ack_ids": ack_ids})
            if nack_ids:
                subscriber.modify_ack_deadline(
                    request={"subscription": sub_path, "ack_ids": nack_ids, "ack_deadline_seconds": 0}
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Pub/Sub pull consumer")
    parser.add_argument("--project", default="augur-495810")
    parser.add_argument("--subscription", default="alert-ingest-local")
    parser.add_argument("--max-messages", type=int, default=5)
    args = parser.parse_args()

    init_tracing()
    try:
        asyncio.run(run(args.project, args.subscription, args.max_messages))
    except KeyboardInterrupt:
        logger.info("Consumer stopped.")


if __name__ == "__main__":
    main()
