# daily_notification_build_command.py
from django.core.management.base import BaseCommand
import asyncio
from django.conf import settings
import redis.asyncio as redis
from users.management.daily_notification_builder import NotificationBuilder
from services.cache_service.DailyNotificationCache import DailyNotificationCache
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Builds daily notifications for all users'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.SUCCESS('Starting notification build process...'))

        try:
            # Create a new event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def run():
                # Initialize cache
                cache = DailyNotificationCache(
                    await redis.from_url(settings.REDIS_URL)
                )

                try:
                    # Create builder with cache
                    builder = NotificationBuilder(cache)

                    # Run the build process
                    await builder.process_all_timezones()
                finally:
                    # Ensure we close the cache connection
                    await cache.close()

            # Run everything
            loop.run_until_complete(run())
            loop.close()

            self.stdout.write(self.style.SUCCESS('Successfully built daily notifications'))

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to build notifications: {str(e)}')
            )
            raise