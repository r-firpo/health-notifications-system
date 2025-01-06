import asyncio
import json
import logging
import os
from datetime import date
from typing import Dict, List, Optional, Tuple, Awaitable, Any

import confluent_kafka
from confluent_kafka import KafkaException
from threading import Thread
import time
from dataclasses import dataclass

from services.cache_service.DailyNotificationCache import DailyNotificationCache
from services.cache_service.NotificationStatus import NotificationStatus

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class PublishMetrics:
    notifications_processed: int = 0
    notifications_published: int = 0
    errors: int = 0
    start_time: Optional[float] = None


class AsyncKafkaProducer:
    """Async Kafka producer implementation based on confluent-kafka with enhanced debugging"""

    def __init__(self, configs: dict, loop=None):
        self._loop = loop or asyncio.get_event_loop()
        logger.info(f"Initializing AsyncKafkaProducer with configs: {configs}")
        self._producer = confluent_kafka.Producer(configs)
        self._cancelled = False
        self._poll_thread = Thread(target=self._poll_loop)
        self._poll_thread.daemon = True  # Make thread daemon so it doesn't block program exit
        self._pending_messages = 0
        self._poll_thread.start()
        logger.info("AsyncKafkaProducer initialized and poll thread started")

    def _poll_loop(self):
        logger.info("Starting producer poll loop")
        while not self._cancelled:
            events_processed = self._producer.poll(0.1)
            if events_processed > 0:
                logger.debug(f"Poll loop processed {events_processed} events")

    async def produce(self, topic: str, key: str, value: str) -> Awaitable[Any]:
        """
        Async produce method with enhanced error handling and logging
        Returns a future that resolves when the message is delivered
        """
        result = self._loop.create_future()
        self._pending_messages += 1

        def ack(err, msg):
            self._pending_messages -= 1
            if err:
                error_msg = f"Failed to deliver message: {err}"
                logger.error(error_msg)
                self._loop.call_soon_threadsafe(
                    result.set_exception, KafkaException(error_msg)
                )
            else:
                success_msg = (
                    f"Successfully delivered message to topic {msg.topic()} "
                    f"partition {msg.partition()} offset {msg.offset()}"
                )
                logger.info(success_msg)
                self._loop.call_soon_threadsafe(result.set_result, msg)

        try:
            logger.info(f"Attempting to produce message to topic {topic} with key {key}")
            self._producer.produce(
                topic=topic,
                key=key.encode('utf-8'),
                value=value.encode('utf-8'),
                on_delivery=ack
            )
            # Trigger immediate flush to catch queue errors
            self._producer.poll(0)

        except BufferError as e:
            error_msg = f"Local producer queue is full: {str(e)}"
            logger.error(error_msg)
            self._pending_messages -= 1
            raise KafkaException(error_msg)
        except Exception as e:
            error_msg = f"Failed to produce message: {str(e)}"
            logger.error(error_msg)
            self._pending_messages -= 1
            raise KafkaException(error_msg)

        return result

    def close(self):
        logger.info("Closing AsyncKafkaProducer")
        self._cancelled = True

        if self._pending_messages > 0:
            logger.warning(f"There are {self._pending_messages} messages pending delivery")
            logger.info("Flushing remaining messages...")
            self._producer.flush(timeout=5)  # 5 second timeout for final flush

        self._poll_thread.join(timeout=5)  # Give poll thread 5 seconds to finish
        if self._poll_thread.is_alive():
            logger.warning("Poll thread did not terminate cleanly")
        logger.info("AsyncKafkaProducer closed")

class DailyNotificationProducer:
    def __init__(
            self,
            notification_cache: DailyNotificationCache,
            kafka_producer: AsyncKafkaProducer,
            batch_size: int = 100,
            rate_limit: int = 1000,
            topic_prefix: str = "daily_notifications.push"
    ):
        self.notification_cache = notification_cache
        self.producer = kafka_producer
        self.batch_size = batch_size
        self.rate_limit = rate_limit
        self.topic_prefix = topic_prefix
        self.metrics = PublishMetrics()

    def _convert_timezone_to_topic(self, timezone: str) -> str:
        """Convert timezone string to Kafka-compatible topic name"""
        return timezone.replace("/", ".")

    async def publish_notifications(self, timezone: str) -> None:
        """
        Publish all staged notifications for a timezone to Kafka
        """
        self.metrics = PublishMetrics(start_time=time.time())
        logger.info(f"Starting notification production for timezone: {timezone}")

        rate_limiter = asyncio.Semaphore(self.rate_limit)

        try:
            while True:
                # Get batch of notifications from cache
                notifications, has_more = await self.notification_cache.get_notifications_batch(
                    timezone,
                    self.batch_size,
                    status_filter=NotificationStatus.STAGED  # Only get STAGED notifications
                )
                logger.info(f"Daily Publisher received {len(notifications)} STAGED notifications")

                if not notifications:
                    if not has_more:
                        logger.info("No more STAGED notifications to process")
                        #break
                    await asyncio.sleep(1)  # Wait a bit longer since we're just checking for new STAGED notifications
                    continue

                self.metrics.notifications_processed += len(notifications)
                topic = f"{self.topic_prefix}"

                # Process each notification in the batch
                update_batch = []
                for notification in notifications.values():
                    try:
                        # Mark as pending before attempting to publish
                        notification.status = NotificationStatus.PENDING

                        # Attempt to publish to Kafka
                        await self.producer.produce(
                            topic=topic,
                            key=notification.user_id,
                            value=json.dumps({
                                'user_id': notification.user_id,
                                'message': notification.message,
                                'timezone': notification.timezone
                            })
                        )

                        # If we got here, publish was successful
                        self.metrics.notifications_published += 1

                    except Exception as e:
                        # If publish failed, mark as failed and log error
                        logger.error(f"Error publishing notification for user {notification.user_id}: {str(e)}")
                        notification.status = NotificationStatus.FAILED
                        notification.error = str(e)
                        self.metrics.errors += 1

                    # Add to batch for cache update
                    update_batch.append(notification)

                # Update cache with results
                if update_batch:
                    await self.notification_cache.update_notification_batch(
                        update_batch,
                        remove_from_processing=True
                    )

                # Log progress periodically
                if self.metrics.notifications_processed % 1000 == 0:
                    self._log_metrics()

        except Exception as e:
            logger.error(f"Error processing notifications for {timezone}: {str(e)}")
            self.metrics.errors += 1
            raise

        finally:
            self._log_metrics()

    def _log_metrics(self) -> None:
        """Log current metrics"""
        if self.metrics.start_time:
            runtime = time.time() - self.metrics.start_time
            logger.info(
                f"Producer Metrics - "
                f"Processed: {self.metrics.notifications_processed}, "
                f"Published: {self.metrics.notifications_published}, "
                f"Errors: {self.metrics.errors}, "
                f"Runtime: {runtime:.2f}s"
            )


if __name__ == "__main__":
    async def main():
        # Kafka configuration with more detailed settings
        kafka_config = {
            "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"),
            "client.id": "daily-notification-producer",
            "queue.buffering.max.messages": 100000,
            "queue.buffering.max.ms": 5000,
            "compression.type": "snappy",
            "linger.ms": 100,
            "acks": "all",  # Wait for all replicas
            "retries": 3,
            "retry.backoff.ms": 1000,
        }

        redis_url = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
        logger.info(f"Starting producer with Kafka config: {kafka_config}")
        logger.info(f"Using Redis URL: {redis_url}")

        notification_cache = None
        kafka_producer = None

        try:
            # Create cache and producer components
            notification_cache = await DailyNotificationCache.create(redis_url)
            logger.info("Successfully created notification cache")

            kafka_producer = AsyncKafkaProducer(kafka_config)
            logger.info("Successfully created Kafka producer")

            notification_producer = DailyNotificationProducer(
                notification_cache=notification_cache,
                kafka_producer=kafka_producer,
                batch_size=100,
                rate_limit=1000,
                topic_prefix=os.environ.get("TOPIC_PREFIX", "daily_notifications.push")
            )

            # Process notifications for a specific timezone
            logger.info("Starting to publish notifications...")
            await notification_producer.publish_notifications("America/Los_Angeles")

        except KafkaException as ke:
            logger.error(f"Kafka error occurred: {str(ke)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            raise
        finally:
            logger.info("Shutting down producer...")
            if kafka_producer:
                try:
                    kafka_producer.close()
                    logger.info("Successfully closed Kafka producer")
                except Exception as e:
                    logger.error(f"Error closing Kafka producer: {str(e)}")

    asyncio.run(main())