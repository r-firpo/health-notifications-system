import json
import logging
import os
import signal
import time
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from confluent_kafka import Consumer, Producer, KafkaError

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


class RetryConfig:
    MAX_RETRIES = 3
    INITIAL_DELAY = 60  # 1 minute
    MAX_DELAY = 3600  # 1 hour

    @staticmethod
    def get_retry_delay(attempt: int) -> int:
        """Calculate delay using exponential backoff"""
        delay = min(
            RetryConfig.INITIAL_DELAY * (2 ** (attempt - 1)),
            RetryConfig.MAX_DELAY
        )
        return delay


class RetryConsumer:
    def __init__(
            self,
            kafka_config: Dict[str, str],
            notification_cache: DailyNotificationCache,
            notification_sender: NotificationSender,
            dead_letter_topic: str,
            consume_topic: str,
            batch_size: int = 50
    ):
        self.kafka_config = kafka_config
        self.notification_cache = notification_cache
        self.notification_sender = notification_sender
        self.dead_letter_topic = dead_letter_topic
        self.consume_topic = consume_topic
        self.batch_size = batch_size
        self.running = True
        self.consumer = None
        self.dlq_producer = None

        self.metrics = {
            'processed': 0,
            'success': 0,
            'failed': 0,
            'sent_to_dlq': 0,
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

    def _should_process_message(self, headers: List[tuple]) -> Tuple[bool, int]:
        """
        Check if message should be processed based on retry timestamp and count
        Returns: (should_process, retry_count)
        """
        retry_count = 0
        retry_timestamp = 0

        if headers:
            for key, value in headers:
                if isinstance(key, bytes):
                    key = key.decode('utf-8')
                if isinstance(value, bytes):
                    value = value.decode('utf-8')

                if key == 'retry_count':
                    retry_count = int(value)
                elif key == 'retry_timestamp':
                    retry_timestamp = int(value)

        # Check if we've exceeded max retries
        if retry_count >= RetryConfig.MAX_RETRIES:
            logger.info(f"Message has reached max retries: {retry_count}")
            return False, retry_count

        # Check if enough time has passed since last retry
        delay = RetryConfig.get_retry_delay(retry_count)
        should_process = (time.time() - retry_timestamp) >= delay

        if not should_process:
            logger.debug(f"Message not ready for retry. Current delay: {delay}s")

        return should_process, retry_count

    async def _send_to_dlq(self, notification: DailyNotification, error: str) -> None:
        """Send permanently failed messages to dead letter queue"""
        try:
            if not self.dlq_producer:
                logger.debug("Initializing DLQ producer")
                self.dlq_producer = Producer({
                    'bootstrap.servers': self.kafka_config['bootstrap.servers']
                })

            dlq_data = {
                'user_id': notification.user_id,
                'message': notification.message,
                'timezone': notification.timezone,
                'error_history': notification.error,
                'final_error': error,
                'attempt_count': notification.attempt_count,
                'last_attempt': notification.last_attempt,
                'failed_at': datetime.now(timezone.utc).isoformat()
            }

            logger.info(f"Sending notification {notification.user_id} to DLQ: {error}")

            self.dlq_producer.produce(
                self.dead_letter_topic,
                key=notification.user_id.encode('utf-8'),
                value=json.dumps(dlq_data).encode('utf-8')
            )
            self.dlq_producer.flush()

            # Update metrics
            self.metrics['sent_to_dlq'] += 1

            # Update cache with final failed status
            notification.status = NotificationStatus.FAILED
            notification.error = f"Moved to DLQ: {error}"
            await self.notification_cache.update_notification_batch([notification])

        except Exception as e:
            logger.error(f"Failed to send notification {notification.user_id} to DLQ: {str(e)}")
            # Still update cache even if DLQ fails
            notification.status = NotificationStatus.FAILED
            notification.error = f"Failed to send to DLQ: {str(e)}"
            await self.notification_cache.update_notification_batch([notification])

    async def _process_retry_batch(self, messages: List[dict]) -> None:
        """Process a batch of retry notifications"""
        if not messages:
            return

        batch_start_time = time.time()
        logger.info(f"Processing retry batch of {len(messages)} messages")

        notifications = []
        for msg in messages:
            try:
                data = json.loads(msg['value'])
                notifications.append(DailyNotification(
                    user_id=data.get('user_id', msg['key']),
                    message=data['message'],
                    status=NotificationStatus.PENDING,
                    timezone=data['timezone'],
                    attempt_count=data.get('attempt_count', 0),
                    error=data.get('error'),
                    last_attempt=time.time()
                ))
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse retry message {msg['key']}: {str(e)}")
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
                    logger.warning(f"Failed to acquire lock for retry notification {notification.user_id}")
                    self.metrics['lock_failures'] += 1

            if not locks_acquired:
                logger.warning("No locks acquired for entire retry batch")
                return

            logger.info(f"Acquired locks for {len(locks_acquired)} retry notifications out of {len(notifications)}")

            # Attempt to send notifications
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
                    logger.info(f"Successfully sent retry notification {notification.user_id}")
                    await self.notification_cache.update_notification_batch(
                        [notification],
                        remove_from_processing=True
                    )
                else:
                    fail_count += 1
                    self.metrics['failed'] += 1
                    if notification.attempt_count >= RetryConfig.MAX_RETRIES:
                        await self._send_to_dlq(
                            notification,
                            f"Max retries ({RetryConfig.MAX_RETRIES}) exceeded: {notification.error}"
                        )
                    else:
                        logger.warning(f"Retry attempt failed for {notification.user_id}: {notification.error}")
                        # Update cache with failed status but keep in retry queue
                        await self.notification_cache.update_notification_batch([notification])

            batch_duration = time.time() - batch_start_time
            logger.info(
                f"Retry batch processing completed. "
                f"Success: {success_count}, Failed: {fail_count}, "
                f"Duration: {batch_duration:.2f}s"
            )

            self.metrics['processed'] += len(sent_notifications)
            self.metrics['batches_processed'] += 1

        except Exception as e:
            logger.error(f"Error processing retry batch: {str(e)}")
            # Update cache for all locked notifications
            for notification in locks_acquired:
                notification.status = NotificationStatus.FAILED
                notification.error = f"Retry batch processing error: {str(e)}"
                await self.notification_cache.update_notification_batch([notification])
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
        """Start consuming retry messages"""
        try:
            logger.info("Initializing retry notification consumer")
            self.consumer = Consumer({
                'bootstrap.servers': self.kafka_config['bootstrap.servers'],
                'group.id': self.kafka_config['group.id'],
                'auto.offset.reset': 'earliest',
                'enable.auto.commit': True
            })
            self.consumer.subscribe([self.consume_topic])

            logger.info(f"Started retry consumer, subscribed to: {self.consume_topic}")

            current_batch = []
            last_batch_time = time.time()

            while self.running:
                msg = self.consumer.poll(timeout=1.0)

                if msg is None:
                    if current_batch and (len(current_batch) >= self.batch_size or
                                          time.time() - last_batch_time > 5):
                        await self._process_retry_batch(current_batch)
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

                # Check if message is ready for retry
                should_process, retry_count = self._should_process_message(msg.headers())

                if not should_process:
                    if retry_count >= RetryConfig.MAX_RETRIES:
                        # Create a notification object for the DLQ
                        data = json.loads(msg.value().decode('utf-8'))
                        notification = DailyNotification(
                            user_id=msg.key().decode('utf-8'),
                            message=data['message'],
                            status=NotificationStatus.FAILED,
                            timezone=data['timezone'],
                            attempt_count=retry_count,
                            error=data.get('error'),
                            last_attempt=time.time()
                        )
                        await self._send_to_dlq(
                            notification,
                            f"Max retries ({RetryConfig.MAX_RETRIES}) exceeded"
                        )
                    continue

                # Add message to current batch
                current_batch.append({
                    'key': msg.key().decode('utf-8'),
                    'value': msg.value().decode('utf-8'),
                    'headers': msg.headers()
                })

                # Process batch if it's full
                if len(current_batch) >= self.batch_size:
                    await self._process_retry_batch(current_batch)
                    current_batch = []
                    last_batch_time = time.time()

                # Log metrics periodically
                if self.metrics['processed'] % 1000 == 0:
                    self._log_metrics()

        except Exception as e:
            logger.error(f"Fatal retry consumer error: {str(e)}")
            raise
        finally:
            if current_batch:
                logger.info(f"Processing final retry batch of {len(current_batch)} messages")
                await self._process_retry_batch(current_batch)

    def _log_metrics(self):
        """Log detailed metrics"""
        runtime = time.time() - self.metrics['start_time']
        logger.info(
            f"Retry Consumer Metrics - "
            f"Processed: {self.metrics['processed']}, "
            f"Success: {self.metrics['success']}, "
            f"Failed: {self.metrics['failed']}, "
            f"Sent to DLQ: {self.metrics['sent_to_dlq']}, "
            f"Batches: {self.metrics['batches_processed']}, "
            f"Lock Failures: {self.metrics['lock_failures']}, "
            f"Runtime: {runtime:.2f}s, "
            f"Avg Success Rate: {(self.metrics['success'] / max(1, self.metrics['processed']) * 100):.1f}%"
        )

    async def shutdown(self):
        """Cleanup resources during shutdown"""
        logger.info("Shutting down retry notification consumer...")
        try:
            if self.consumer:
                self.consumer.close()
            if self.dlq_producer:
                self.dlq_producer.flush()
                self.dlq_producer.close()
        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}")
        finally:
            self._log_metrics()
            logger.info("Retry notification consumer shutdown complete")


if __name__ == "__main__":
    # Kafka configuration
    kafka_config = {
        'bootstrap.servers': os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092'),
        'group.id': os.environ.get('KAFKA_GROUP_ID', 'notification-retry-consumer-group'),
        'auto.offset.reset': 'earliest'
    }
    consume_topic = os.environ.get('KAFKA_TOPIC', 'daily_notifications.retry')


    async def main():
        try:
            logger.info("Initializing retry consumer components")
            # Create cache and consumer components
            notification_cache = await DailyNotificationCache.create(
                redis_url=os.environ.get('REDIS_URL', 'redis://redis:6379/0')
            )
            notification_sender = NotificationSender(
                api_url=os.environ.get('NOTIFICATION_API_URL', 'https://api.notification-service.com/notify'),
                api_key=os.environ.get('NOTIFICATION_API_KEY', 'your-api-key')
            )

            # Create and start retry consumer
            consumer = RetryConsumer(
                kafka_config,
                notification_sender=notification_sender,
                notification_cache=notification_cache,
                consume_topic=consume_topic,
                dead_letter_topic=os.environ.get('DEAD_LETTER_TOPIC', 'daily_notifications.dead_letter'),
                batch_size=25
            )

            await consumer.start()

        except KeyboardInterrupt:
            logger.info("Shutting down retry consumer...")
        except Exception as e:
            logger.error(f"Error running retry consumer: {str(e)}")
            raise
        finally:
            if 'consumer' in locals():
                await consumer.shutdown()

    asyncio.run(main())