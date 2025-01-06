import os
import sys
import redis
import logging
from typing import Optional
import signal
from time import sleep

# Import the DailyNotificationCache
from DailyNotificationCache import DailyNotificationCache

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CacheService:
    def __init__(self, redis_url: str, ttl: Optional[int] = None):
        self.running = True
        self.redis_client = redis.from_url(redis_url)
        self.notification_cache = DailyNotificationCache(
            redis_client=self.redis_client,
            ttl=ttl or 60 * 60 * 24  # Default 24 hours
        )

        # Setup signal handlers
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def shutdown(self, signum, frame):
        logger.info("Received shutdown signal, cleaning up...")
        self.running = False

    def run(self):
        logger.info("Starting Notification Cache Service...")

        try:
            while self.running:
                # Perform any periodic cleanup or maintenance
                # For example, clearing expired entries
                try:
                    self.notification_cache.cleanup_processing_set("*")
                except Exception as e:
                    logger.error(f"Error during cleanup: {e}")

                sleep(300)  # Sleep for 5 minutes between cleanup cycles

        except Exception as e:
            logger.error(f"Service error: {e}")
            raise
        finally:
            logger.info("Shutting down Notification Cache Service...")


if __name__ == "__main__":
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    ttl = int(os.getenv("CACHE_TTL", 86400))  # 24 hours in seconds

    service = CacheService(redis_url=redis_url, ttl=ttl)
    service.run()