from datetime import date, datetime
import json
from typing import Dict, List
import logging
import time
from asgiref.sync import sync_to_async
import pytz
from django.conf import settings

from services.cache_service.DailyNotificationCache import DailyNotificationCache
from services.cache_service.DailyNotificationCache import DailyNotification
from services.llm.llm_service import HealthNotificationLLMService
from users.models import NotificationPreference
from services.cache_service.NotificationStatus import NotificationStatus
from users.models import User

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NotificationBuilder:
    def __init__(self, notification_cache: DailyNotificationCache):
        self.notification_cache = notification_cache
        self.metrics = {
            'users_processed': 0,
            'notifications_built': 0,
            'errors': 0,
            'start_time': None
        }

    def _get_date_for_timezone(self, timezone_str: str) -> date:
        """Get current date for a specific timezone"""
        tz = pytz.timezone(timezone_str)
        return datetime.now(tz).date()

    @sync_to_async
    def _get_users_batch(self, offset: int, batch_size: int) -> List[User]:
        """Get a batch of users with their notification preferences"""
        return list(User.objects
                    .select_related('notification_preferences')
                    .filter(
            notification_preferences__opt_in=True,
            notification_preferences__push_enabled=True
        )
                    .order_by('timezone', 'id')[offset:offset + batch_size])

    async def _build_notification_payload(self, user: User) -> List[DailyNotification]:
        """Build notification payloads using LLM for personalized messages"""
        notifications = []

        try:
            llm_service = HealthNotificationLLMService.get_instance()

            # Prepare user data for LLM
            user_data = {
                'name': user.name,
                'age': user.age,
                'activity_level': user.activity_level,
                'health_goals': user.health_goals
            }

            # Generate personalized message
            #TODO this is what it would look like with a valid API key
            # message = await llm_service.generate_health_notification(user_data)
            message = "your daily message"
            notifications.append(DailyNotification(
                user_id=str(user.id),
                message=message,
                status=NotificationStatus.STAGED,
                timezone=user.timezone,
                sent_at=None,
                error=None,
                attempt_count=0,
                last_attempt=None
            ))
            logger.info(f"Built notification for user {user.id} in timezone {user.timezone}")

        except Exception as e:
            logger.error(f"Error building notification for user {user.id}: {str(e)}")
            self.metrics['errors'] += 1

        return notifications

    async def process_all_timezones(self):
        """Process notifications for all timezones"""
        try:
            self.metrics['start_time'] = time.time()
            logger.info("Starting notification build process")

            offset = 0
            batch_size = 100

            while True:
                users = await self._get_users_batch(offset, batch_size)

                if not users:
                    break

                # Group users by timezone
                timezone_groups = {}
                for user in users:
                    if user.timezone not in timezone_groups:
                        timezone_groups[user.timezone] = []
                    timezone_groups[user.timezone].append(user)

                # Process each timezone group
                for timezone, timezone_users in timezone_groups.items():
                    current_date = self._get_date_for_timezone(timezone)
                    logger.info(
                        f"Processing {len(timezone_users)} users for timezone {timezone} (current date: {current_date})")

                    notifications = []
                    for user in timezone_users:
                        try:
                            notification_batch = await self._build_notification_payload(user)
                            notifications.extend(notification_batch)
                            self.metrics['users_processed'] += 1
                        except Exception as e:
                            logger.error(f"Error building notification for user {user.id}: {str(e)}")
                            self.metrics['errors'] += 1
                            continue

                    if notifications:
                        try:
                            await self.notification_cache.update_notification_batch(notifications)
                            self.metrics['notifications_built'] += len(notifications)
                        except Exception as e:
                            logger.error(f"Error caching notifications batch: {str(e)}")
                            self.metrics['errors'] += 1

                offset += batch_size
                self._log_metrics()

            logger.info("Completed notification build process")
            self._log_metrics()

        except Exception as e:
            self.metrics['errors'] += 1
            logger.error(f"Error processing notifications: {str(e)}")
            raise

    def _log_metrics(self):
        """Log current metrics"""
        if self.metrics['start_time'] is None:
            return

        runtime = time.time() - self.metrics['start_time']
        logger.info(
            f"Build Metrics - Users Processed: {self.metrics['users_processed']}, "
            f"Notifications Built: {self.metrics['notifications_built']}, "
            f"Errors: {self.metrics['errors']}, "
            f"Runtime: {runtime:.2f}s"
        )