import logging
import asyncio
import random
from typing import List
from datetime import datetime
from services.cache_service.DailyNotificationCache import DailyNotification, NotificationStatus

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NotificationSender:
    def __init__(self, api_url: str, api_key: str):
        # Currently unused but kept for future implementation
        self.api_url = api_url
        self.api_key = api_key

    @staticmethod
    async def send_batch(
            notifications: List[DailyNotification],
            batch_size: int,
            force_fail: bool = False
    ) -> List[DailyNotification]:
        """
        Simulates sending notifications with configurable failure rate

        Args:
            notifications: List of notifications to send
            batch_size: Maximum batch size
            force_fail: If True, approximately half of the notifications will fail
        """
        logger.info(f"Sending batch of {len(notifications)} notifications (force_fail={force_fail})")

        await asyncio.sleep(0.1)

        current_time = datetime.now().timestamp()

        for notification in notifications:
            should_fail = force_fail and random.random() < 0.5

            if should_fail:
                notification.status = NotificationStatus.FAILED
                notification.error = "Simulated failure for testing"
                logger.warning(f"Notification failed for user {notification.user_id}: {notification.error}")
            else:
                notification.status = NotificationStatus.COMPLETED
                notification.error = None
                logger.info(f"Successfully sent notification to user {notification.user_id}")

            notification.sent_at = current_time
            notification.attempt_count += 1
            notification.last_attempt = current_time

        success_count = sum(1 for n in notifications if n.status == NotificationStatus.COMPLETED)
        fail_count = len(notifications) - success_count
        logger.info(f"Batch complete: {success_count} succeeded, {fail_count} failed")

        return notifications

    @staticmethod
    async def shutdown():
        """Cleanup method for graceful shutdown"""
        logger.info("Shutting down notification sender")