# DailyNotificationCache.py
import logging
import pytz
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import redis.asyncio as redis
import json
from dataclasses import dataclass

from services.cache_service.NotificationStatus import NotificationStatus

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class DailyNotification:
    user_id: str
    message: str
    status: NotificationStatus
    timezone: str  # This should be a valid pytz timezone string
    sent_at: Optional[float] = None
    error: Optional[str] = None
    attempt_count: int = 0
    last_attempt: Optional[float] = None


class DailyNotificationCache:
    def __init__(self, redis_client, ttl: int = 60 * 60 * 24):
        """Initialize with a Redis client and TTL

        Args:
            redis_client: An initialized Redis client instance
            ttl: Time to live for cache entries in seconds
        """
        self.redis = redis_client
        self.ttl = ttl
        self._lock_ttl = 30

    @classmethod
    async def create(cls, redis_url: str, ttl: int = 60 * 60 * 24) -> 'DailyNotificationCache':
        """Factory method to create and initialize the cache asynchronously"""
        redis_client = await redis.from_url(redis_url)
        return cls(redis_client, ttl)

    def _validate_timezone(self, tz_str: str) -> pytz.BaseTzInfo:
        """Validate and return a proper timezone object"""
        try:
            return pytz.timezone(tz_str)
        except pytz.exceptions.UnknownTimeZoneError:
            logger.error(f"Invalid timezone: {tz_str}")
            # Default to UTC if timezone is invalid
            return pytz.UTC

    def _get_cache_keys(self, timezone_str: str) -> Tuple[str, str, str, str]:
        """Get Redis keys for notifications and their states"""
        tz = self._validate_timezone(timezone_str)
        current_date = datetime.now(tz).date().isoformat()
        base = f"daily_notifications:{timezone_str}:{current_date}"
        return (
            base,  # Main cache key (to_process)
            f"{base}:processing",  # Processing set key
            f"{base}:completed",  # Completed set key
            f"{base}:locks"  # Lock key prefix
        )

    async def update_notification_batch(
            self,
            notifications: List[DailyNotification],
            remove_from_processing: bool = True
    ) -> None:
        """Update a batch of notifications atomically"""
        if not notifications:
            return

        # Group notifications by timezone
        by_timezone = {}
        for notification in notifications:
            if notification.timezone not in by_timezone:
                by_timezone[notification.timezone] = []
            by_timezone[notification.timezone].append(notification)

        # Update each timezone's cache
        for timezone, timezone_notifications in by_timezone.items():
            cache_key, processing_key, completed_key, _ = self._get_cache_keys(timezone)

            async with self.redis.pipeline() as pipe:
                # Update notifications
                for notification in timezone_notifications:
                    data = {
                        'user_id': notification.user_id,
                        'message': notification.message,
                        'status': notification.status.value,
                        'timezone': notification.timezone,
                        'sent_at': notification.sent_at,
                        'error': notification.error,
                        'attempt_count': notification.attempt_count,
                        'last_attempt': notification.last_attempt
                    }
                    await pipe.hset(cache_key, notification.user_id, json.dumps(data))
                    logger.info(f"set key {cache_key}, with user {notification.user_id}, with data:{json.dumps(data)}")

                    if remove_from_processing:
                        await pipe.srem(processing_key, notification.user_id)

                # Refresh TTL
                await pipe.expire(cache_key, self.ttl)
                await pipe.expire(processing_key, self.ttl)

                # Execute transaction
                await pipe.execute()

    async def get_notifications_batch(
            self,
            timezone: str,
            batch_size: int = 100,
            status_filter: Optional[NotificationStatus] = None
    ) -> Tuple[Dict[str, DailyNotification], bool]:
        """
        Get a batch of notifications for processing

        Args:
            timezone: Timezone to get notifications for
            batch_size: Maximum number of notifications to return
            status_filter: Optional status to filter by (e.g., STAGED for producer)
        """
        notifications = {}
        cache_key, processing_key, completed_key, _ = self._get_cache_keys(timezone)

        try:
            # Get all available notification IDs and processing IDs
            all_ids = await self.redis.hkeys(cache_key)
            processing_ids = await self.redis.smembers(processing_key)

            # Filter out processing IDs
            available_ids = set([id.decode('utf-8') if isinstance(id, bytes) else id
                                 for id in all_ids]) - set(
                [id.decode('utf-8') if isinstance(id, bytes) else id
                 for id in processing_ids])

            if not available_ids:
                return {}, False

            # Get all notifications data
            filtered_notifications = {}
            for user_id in available_ids:
                notification_data = await self.redis.hget(cache_key, user_id)
                if notification_data:
                    try:
                        # Handle both string and bytes responses
                        if isinstance(notification_data, bytes):
                            data = json.loads(notification_data.decode('utf-8'))
                        else:
                            data = json.loads(notification_data)

                        # Only include notifications matching status_filter if provided
                        if status_filter and NotificationStatus(data['status']) != status_filter:
                            continue

                        filtered_notifications[user_id] = DailyNotification(
                            user_id=user_id,
                            message=data['message'],
                            status=NotificationStatus(data['status']),
                            timezone=data['timezone'],
                            sent_at=data.get('sent_at'),
                            error=data.get('error'),
                            attempt_count=data.get('attempt_count', 0),
                            last_attempt=data.get('last_attempt')
                        )

                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logger.error(f"Error processing notification for user {user_id}: {str(e)}")
                        continue

            # Take only batch_size notifications
            batch_ids = list(filtered_notifications.keys())[:batch_size]
            notifications = {id: filtered_notifications[id] for id in batch_ids}

            return notifications, len(filtered_notifications) > batch_size

        except Exception as e:
            logger.error(f"Error fetching notifications batch: {str(e)}")
            raise

    async def cleanup_processing_set(self, timezone: str) -> None:
        """Clean up processing set for notifications that are completed or failed"""
        cache_key, processing_key, completed_key, _ = self._get_cache_keys(timezone)

        # Get processing IDs
        processing_ids = await self.redis.smembers(processing_key)
        if not processing_ids:
            return

        async with self.redis.pipeline() as pipe:
            for user_id in processing_ids:
                user_id = user_id.decode('utf-8') if isinstance(user_id, bytes) else user_id
                notification_data = await self.redis.hget(cache_key, user_id)

                if notification_data:
                    data = json.loads(notification_data.decode('utf-8') if isinstance(notification_data,
                                                                                      bytes) else notification_data)
                    status = NotificationStatus(data['status'])

                    if status in (NotificationStatus.COMPLETED, NotificationStatus.FAILED):
                        await pipe.srem(processing_key, user_id)

            await pipe.execute()

    async def acquire_lock(self, user_id: str, timezone: str) -> bool:
        """Acquire a lock for processing a user's notification"""
        _, _, _, lock_key_prefix = self._get_cache_keys(timezone)
        lock_key = f"{lock_key_prefix}:{user_id}"

        current_time = datetime.now().timestamp()
        return await self.redis.set(
            lock_key,
            str(current_time),
            ex=self._lock_ttl,
            nx=True
        )

    async def release_lock(self, user_id: str, timezone: str) -> None:
        """Release a processing lock"""
        _, _, _, lock_key_prefix = self._get_cache_keys(timezone)
        lock_key = f"{lock_key_prefix}:{user_id}"
        await self.redis.delete(lock_key)

    async def close(self):
        """Close the Redis connection"""
        await self.redis.close()