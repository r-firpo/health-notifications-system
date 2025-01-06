# inspect_notification_cache.py
import json
from datetime import datetime, date
import pytz
from django.core.management.base import BaseCommand
import settings
import redis
import logging
from typing import Dict, List, Optional, Set

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Inspect and manage notification cache'

    def add_arguments(self, parser):
        parser.add_argument(
            '--action',
            type=str,
            choices=['list', 'clear', 'stats', 'dump'],
            default='stats',
            help='Action to perform on cache'
        )
        parser.add_argument(
            '--timezone',
            type=str,
            help='Specific timezone to inspect (e.g., America/New_York)'
        )
        parser.add_argument(
            '--date',
            type=str,
            help='Specific date to inspect (YYYY-MM-DD), defaults to timezone-specific current date'
        )

    def _get_redis_client(self):
        return redis.from_url(settings.REDIS_URL)

    def _get_date_for_timezone(self, timezone: str) -> str:
        """Get current date for a specific timezone"""
        tz = pytz.timezone(timezone)
        return datetime.now(tz).date().isoformat()

    def _get_cache_keys(self, timezone: str, date_str: Optional[str] = None) -> Dict[str, str]:
        """Generate all cache keys for a given timezone and date"""
        if date_str:
            cache_date = date_str
        else:
            cache_date = self._get_date_for_timezone(timezone)

        base = f"daily_notifications:{timezone}:{cache_date}"
        return {
            'main': base,
            'processing': f"{base}:processing",
            'completed': f"{base}:completed",
            'locks': f"{base}:locks"
        }

    def _get_all_timezones(self, redis_client) -> List[str]:
        """Get all timezones currently in cache"""
        keys = redis_client.keys("daily_notifications:*")
        timezones = set()
        for key in keys:
            parts = key.decode().split(':')
            if len(parts) > 1:
                timezones.add(parts[1])
        return sorted(list(timezones))

    def _get_dates_for_timezone(self, redis_client, timezone: str) -> Set[str]:
        """Get all dates available for a timezone"""
        pattern = f"daily_notifications:{timezone}:*"
        keys = redis_client.keys(pattern)
        dates = set()
        for key in keys:
            parts = key.decode().split(':')
            if len(parts) > 2:
                dates.add(parts[2])
        return dates

    def handle(self, *args, **options):
        redis_client = self._get_redis_client()
        action = options['action']
        timezone = options['timezone']
        date_str = options['date']

        try:
            if action == 'clear':
                self._clear_cache(redis_client, timezone, date_str)
            elif action == 'list':
                self._list_cache(redis_client, timezone, date_str)
            elif action == 'dump':
                self._dump_cache(redis_client, timezone, date_str)
            else:  # stats
                self._show_stats(redis_client, timezone, date_str)

        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Error: {str(e)}'))
            raise

    def _clear_cache(self, redis_client, timezone: Optional[str], date_str: Optional[str]):
        """Clear cache entries"""
        if timezone:
            timezones = [timezone]
        else:
            timezones = self._get_all_timezones(redis_client)

        for tz in timezones:
            dates = {date_str} if date_str else self._get_dates_for_timezone(redis_client, tz)
            for dt in dates:
                keys = self._get_cache_keys(tz, dt)
                for key in keys.values():
                    redis_client.delete(key)
                self.stdout.write(self.style.SUCCESS(f'Cleared cache for timezone: {tz}, date: {dt}'))

    def _list_cache(self, redis_client, timezone: Optional[str], date_str: Optional[str]):
        """List cache entries"""
        if timezone:
            timezones = [timezone]
        else:
            timezones = self._get_all_timezones(redis_client)

        for tz in timezones:
            dates = {date_str} if date_str else self._get_dates_for_timezone(redis_client, tz)
            for dt in dates:
                self.stdout.write(self.style.SUCCESS(f'\nTimezone: {tz}, Date: {dt}'))
                keys = self._get_cache_keys(tz, dt)

                # Main notifications
                main_count = redis_client.hlen(keys['main'])
                self.stdout.write(f"  Notifications: {main_count}")

                # Processing set
                processing = redis_client.smembers(keys['processing'])
                self.stdout.write(f"  Processing: {len(processing)} notifications")

                # Completed set
                completed = redis_client.smembers(keys['completed'])
                self.stdout.write(f"  Completed: {len(completed)} notifications")

                # Active locks
                locks = redis_client.keys(f"{keys['locks']}:*")
                self.stdout.write(f"  Active locks: {len(locks)}")

    def _show_stats(self, redis_client, timezone: Optional[str], date_str: Optional[str]):
        """Show detailed statistics"""
        if timezone:
            timezones = [timezone]
        else:
            timezones = self._get_all_timezones(redis_client)

        total_stats = {
            'notifications': 0,
            'processing': 0,
            'completed': 0,
            'locks': 0
        }

        for tz in timezones:
            dates = {date_str} if date_str else self._get_dates_for_timezone(redis_client, tz)
            for dt in dates:
                self.stdout.write(self.style.SUCCESS(f'\nTimezone: {tz}, Date: {dt}'))
                keys = self._get_cache_keys(tz, dt)

                # Get all notifications
                notifications = redis_client.hgetall(keys['main'])
                status_counts = {'STAGED': 0, 'PENDING': 0, 'COMPLETED': 0, 'FAILED': 0}

                for _, value in notifications.items():
                    notification = json.loads(value)
                    status = notification.get('status', 'UNKNOWN')
                    status_counts[status] = status_counts.get(status, 0) + 1

                # Print status distribution
                for status, count in status_counts.items():
                    self.stdout.write(f"  {status}: {count}")
                    total_stats['notifications'] += count

                # Processing and completed counts
                processing = len(redis_client.smembers(keys['processing']))
                completed = len(redis_client.smembers(keys['completed']))
                locks = len(redis_client.keys(f"{keys['locks']}:*"))

                total_stats['processing'] += processing
                total_stats['completed'] += completed
                total_stats['locks'] += locks

        # Print totals
        self.stdout.write(self.style.SUCCESS('\nTotal Statistics:'))
        for key, value in total_stats.items():
            self.stdout.write(f"  {key.capitalize()}: {value}")

    def _dump_cache(self, redis_client, timezone: Optional[str], date_str: Optional[str]):
        """Dump full cache contents for inspection"""
        if timezone:
            timezones = [timezone]
        else:
            timezones = self._get_all_timezones(redis_client)

        for tz in timezones:
            dates = {date_str} if date_str else self._get_dates_for_timezone(redis_client, tz)
            for dt in dates:
                self.stdout.write(self.style.SUCCESS(f'\nTimezone: {tz}, Date: {dt}'))
                keys = self._get_cache_keys(tz, dt)

                # Dump main notifications
                notifications = redis_client.hgetall(keys['main'])
                if notifications:
                    self.stdout.write("\nNotifications:")
                    for user_id, value in notifications.items():
                        notification = json.loads(value)
                        self.stdout.write(f"\nUser {user_id.decode()}:")
                        self.stdout.write(json.dumps(notification, indent=2))

                # Dump processing set
                processing = redis_client.smembers(keys['processing'])
                if processing:
                    self.stdout.write("\nProcessing Set:")
                    for item in processing:
                        self.stdout.write(f"  {item.decode()}")

                # Dump completed set
                completed = redis_client.smembers(keys['completed'])
                if completed:
                    self.stdout.write("\nCompleted Set:")
                    for item in completed:
                        self.stdout.write(f"  {item.decode()}")