import asyncio
import os
import time
import logging
import json
import signal
from datetime import datetime, timezone
from typing import Dict, List

from confluent_kafka import Producer, Consumer, KafkaError

from services.cache_service.DailyNotificationCache import (
    DailyNotificationCache,
    DailyNotification,
    NotificationStatus
)
from services.kafka_consumers.NotificationSender import NotificationSender

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DailyNotificationConsumer:
    def __init__(
            self,
            kafka_config: Dict[str, str],
            notification_cache: DailyNotificationCache,
            notification_sender: NotificationSender,
            retry_topic: str,
            consume_topic: str,
            batch_size: int = 50
    ):
        self.kafka_config = kafka_config
        self.notification_cache = notification_cache
        self.notification_sender = notification_sender
        self.retry_topic = retry_topic
        self.consume_topic = consume_topic
        self.batch_size = batch_size
        self.running = True
        self.consumer = None
        self.retry_producer = None

        self.metrics = {
            'processed': 0,
            'success': 0,
            'failed': 0,
            'retried': 0,
            'batches_processed': 0,
            'lock_failures': 0,
            'start_time': time.time()
        }

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info("Received shutdown signal")
        self.running = False

    async def _schedule_retry(self, notification: DailyNotification) -> None:
        """Schedule a failed notification for retry"""
        try:
            if not self.retry_producer:
                logger.debug("Initializing retry producer")
                self.retry_producer = Producer({
                    'bootstrap.servers': self.kafka_config['bootstrap.servers']
                })

            retry_data = {
                'user_id': notification.user_id,
                'message': notification.message,
                'timezone': notification.timezone,
                'error': notification.error,
                'attempt_count': 1,  # First retry attempt
                'last_attempt': datetime.now(timezone.utc).timestamp(),
                'original_error': notification.error
            }

            logger.info(f"Scheduling retry for notification {notification.user_id} due to: {notification.error}")

            self.retry_producer.produce(
                self.retry_topic,
                key=notification.user_id.encode('utf-8'),
                value=json.dumps(retry_data).encode('utf-8'),
                headers=[
                    ('retry_count', b'1'),
                    ('retry_timestamp', str(int(time.time())).encode())
                ]
            )
            self.retry_producer.flush()
            self.metrics['retried'] += 1
            logger.info(f"Successfully scheduled retry for notification {notification.user_id}")

        except Exception as e:
            logger.error(f"Failed to schedule retry for notification {notification.user_id}: {str(e)}")
            # Since retry scheduling failed, mark as failed in cache
            notification.status = NotificationStatus.FAILED
            notification.error = f"Failed to schedule retry: {str(e)}"
            await self.notification_cache.update_notification_batch([notification])
            self.metrics['failed'] += 1

    async def _process_batch(self, messages: List[dict]) -> None:
        """Process a batch of notifications"""
        if not messages:
            return

        batch_start_time = time.time()
        logger.info(f"Processing batch of {len(messages)} messages")

        notifications = []
        for msg in messages:
            try:
                data = json.loads(msg['value'])
                notifications.append(DailyNotification(
                    user_id=data.get('user_id', msg['key']),
                    message=data['message'],
                    status=NotificationStatus.PENDING,  # Make sure we set this to PENDING
                    timezone=data['timezone'],
                    attempt_count=0,
                    last_attempt=time.time()
                ))
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse message {msg['key']}: {str(e)}")
                continue

        try:
            # Acquire locks for the batch
            locks_acquired = []
            for notification in notifications:
                if await self.notification_cache.acquire_lock(
                        notification.user_id,
                        notification.timezone
                ):
                    locks_acquired.append(notification)
                else:
                    logger.warning(f"Failed to acquire lock for notification {notification.user_id}")
                    self.metrics['lock_failures'] += 1

            if not locks_acquired:
                logger.warning("No locks acquired for entire batch")
                return

            logger.info(f"Acquired locks for {len(locks_acquired)} notifications out of {len(notifications)}")

            # Send notifications
            sent_notifications = await self.notification_sender.send_batch(
                locks_acquired,
                self.batch_size
            )

            # Process results
            success_count = 0
            fail_count = 0
            for notification in sent_notifications:
                if notification.status == NotificationStatus.COMPLETED:
                    success_count += 1
                    self.metrics['success'] += 1
                    logger.info(
                        f"Attempting to update cache for notification {notification.user_id} with status {notification.status}")
                    # Update cache with success status
                    try:
                        await self.notification_cache.update_notification_batch(
                            [notification],
                            remove_from_processing=True
                        )
                        logger.info(f"Successfully updated cache for notification {notification.user_id}")
                    except Exception as e:
                        logger.error(f"Failed to update cache for notification {notification.user_id}: {str(e)}")
                else:
                    fail_count += 1
                    logger.warning(f"Failed to send notification {notification.user_id}: {notification.error}")
                    # Schedule for retry
                    await self._schedule_retry(notification)

            batch_duration = time.time() - batch_start_time
            logger.info(
                f"Batch processing completed. "
                f"Success: {success_count}, Failed: {fail_count}, "
                f"Duration: {batch_duration:.2f}s"
            )

            self.metrics['processed'] += len(sent_notifications)
            self.metrics['batches_processed'] += 1

        except Exception as e:
            logger.error(f"Error processing batch: {str(e)}")
            # Attempt to schedule retries for all locked notifications
            for notification in locks_acquired:
                notification.status = NotificationStatus.FAILED
                notification.error = f"Batch processing error: {str(e)}"
                await self._schedule_retry(notification)
        finally:
            # Release locks
            for notification in locks_acquired:
                try:
                    await self.notification_cache.release_lock(
                        notification.user_id,
                        notification.timezone
                    )
                except Exception as e:
                    logger.error(f"Failed to release lock for {notification.user_id}: {str(e)}")

    async def start(self):
        """Start consuming messages"""
        try:
            logger.info("Initializing daily notification consumer")
            self.consumer = Consumer({
                'bootstrap.servers': self.kafka_config['bootstrap.servers'],
                'group.id': self.kafka_config['group.id'],
                'auto.offset.reset': 'earliest',
                'enable.auto.commit': True
            })
            self.consumer.subscribe([self.consume_topic])

            logger.info(f"Started consumer, subscribed to: {self.consume_topic}")

            current_batch = []
            last_batch_time = time.time()

            while self.running:
                msg = self.consumer.poll(timeout=1.0)

                if msg is None:
                    if current_batch and (len(current_batch) >= self.batch_size or
                                          time.time() - last_batch_time > 5):
                        await self._process_batch(current_batch)
                        current_batch = []
                        last_batch_time = time.time()
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        logger.debug(f"Reached end of partition for {self.consume_topic}")
                        continue
                    else:
                        logger.error(f"Consumer error: {msg.error()}")
                        continue

                # Add message to current batch
                current_batch.append({
                    'key': msg.key().decode('utf-8'),
                    'value': msg.value().decode('utf-8')
                })

                # Process batch if it's full
                if len(current_batch) >= self.batch_size:
                    await self._process_batch(current_batch)
                    current_batch = []
                    last_batch_time = time.time()

                # Log metrics periodically
                if self.metrics['processed'] % 1000 == 0:
                    self._log_metrics()

        except Exception as e:
            logger.error(f"Fatal consumer error: {str(e)}")
            raise
        finally:
            if current_batch:
                logger.info(f"Processing final batch of {len(current_batch)} messages")
                await self._process_batch(current_batch)

    def _log_metrics(self):
        """Log detailed metrics"""
        runtime = time.time() - self.metrics['start_time']
        logger.info(
            f"Consumer Metrics - "
            f"Processed: {self.metrics['processed']}, "
            f"Success: {self.metrics['success']}, "
            f"Failed: {self.metrics['failed']}, "
            f"Retried: {self.metrics['retried']}, "
            f"Batches: {self.metrics['batches_processed']}, "
            f"Lock Failures: {self.metrics['lock_failures']}, "
            f"Runtime: {runtime:.2f}s, "
            f"Avg Success Rate: {(self.metrics['success'] / max(1, self.metrics['processed']) * 100):.1f}%"
        )

    async def shutdown(self):
        """Cleanup resources during shutdown"""
        logger.info("Shutting down daily notification consumer...")
        try:
            if self.consumer:
                self.consumer.close()
            if self.retry_producer:
                self.retry_producer.flush()
                self.retry_producer.close()
        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}")
        finally:
            self._log_metrics()
            logger.info("Daily notification consumer shutdown complete")


if __name__ == "__main__":
    # Kafka configuration
    kafka_config = {
        "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"),
        "group.id": os.environ.get("KAFKA_GROUP_ID", "notification-consumer-group"),
        "auto.offset.reset": "earliest"
    }
    consume_topic = os.environ.get("KAFKA_TOPIC", "daily_notifications.push")


    async def main():
        try:
            logger.info("Initializing notification consumer components")
            # Create cache and consumer components
            notification_cache = await DailyNotificationCache.create(
                redis_url=os.environ.get('REDIS_URL', 'redis://redis:6379/0')
            )
            notification_sender = NotificationSender(
                api_url=os.environ.get("NOTIFICATION_API_URL", "https://api.notification-service.com/notify"),
                api_key=os.environ.get("NOTIFICATION_API_KEY", "your-api-key")
            )

            # Create and start the consumer
            consumer = DailyNotificationConsumer(
                kafka_config=kafka_config,
                notification_cache=notification_cache,
                notification_sender=notification_sender,
                consume_topic=consume_topic,
                retry_topic=os.environ.get("RETRY_TOPIC", "daily_notifications.retry"),
                batch_size=50
            )

            logger.info("Starting notification consumer")
            await consumer.start()

        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        except Exception as e:
            logger.error(f"Error running consumer: {str(e)}")
            raise
        finally:
            if 'consumer' in locals():
                await consumer.shutdown()


    asyncio.run(main())