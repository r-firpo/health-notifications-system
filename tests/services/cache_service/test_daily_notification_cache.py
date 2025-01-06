import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import json
from datetime import datetime
from typing import Optional

from services.cache_service.NotificationStatus import NotificationStatus
from services.cache_service.DailyNotificationCache import DailyNotification


class AsyncPipelineMock:
    """Mock for Redis pipeline that properly handles async context manager"""

    def __init__(self):
        self.execute = AsyncMock(return_value=[True])
        self.hget = AsyncMock()
        self.hset = AsyncMock()
        self.sadd = AsyncMock()
        self.srem = AsyncMock()
        self.expire = AsyncMock()
        self.watch = AsyncMock()
        self.multi = AsyncMock()
        self.called = False

    async def __aenter__(self):
        self.called = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockRedis:
    """Mock for Redis client that properly handles async pipeline"""

    def __init__(self):
        self.set = AsyncMock()
        self.get = AsyncMock()
        self.delete = AsyncMock()
        self.hget = AsyncMock()
        self.hset = AsyncMock()
        self.hdel = AsyncMock()
        self.hkeys = AsyncMock(return_value=[])
        self.smembers = AsyncMock(return_value=set())
        self.expire = AsyncMock()
        self.close = AsyncMock()

        # Create pipeline instance that will be reused
        self._pipeline = AsyncPipelineMock()

    def pipeline(self):
        """Return the pipeline mock as an async context manager"""
        return self._pipeline


class TestDailyNotificationCache:
    @pytest.fixture
    async def cache(self):
        """Setup test environment before each test"""
        redis_url = "redis://fake:6379"
        ttl = 3600

        # Create mock Redis client
        redis_mock = MockRedis()

        # Mock redis.from_url to return our mock
        async def mock_from_url(*args, **kwargs):
            return redis_mock

        # Patch redis.from_url
        with patch('redis.asyncio.from_url', mock_from_url):
            from services.cache_service.DailyNotificationCache import DailyNotificationCache
            cache = await DailyNotificationCache.create(redis_url=redis_url, ttl=ttl)

            # Store reference to mock for assertions
            cache._redis_mock = redis_mock
            cache._pipeline_mock = redis_mock._pipeline

            yield cache
            await cache.close()

    @pytest.mark.asyncio
    async def test_acquire_lock(self, cache):
        """Test successful lock acquisition"""
        cache._redis_mock.set.return_value = True
        result = await cache.acquire_lock("user123", "UTC")
        assert result is True

        # Verify the set call
        cache._redis_mock.set.assert_called_once()
        args, kwargs = cache._redis_mock.set.call_args
        assert "user123" in args[0]
        assert kwargs.get('ex') == 30
        assert kwargs.get('nx') is True

    @pytest.mark.asyncio
    async def test_acquire_lock_fails(self, cache):
        """Test failed lock acquisition"""
        cache._redis_mock.set.return_value = False
        result = await cache.acquire_lock("user123", "UTC")
        assert result is False

    @pytest.mark.asyncio
    async def test_release_lock(self, cache):
        """Test lock release"""
        await cache.release_lock("user123", "UTC")
        cache._redis_mock.delete.assert_called_once()
        args = cache._redis_mock.delete.call_args[0]
        assert "user123" in args[0]

    @pytest.mark.asyncio
    async def test_get_notifications_batch_success(self, cache):
        """Test successfully fetching a batch of notifications"""
        # Sample notification for test
        sample_notification = DailyNotification(
            user_id="123",
            message="Test notification",
            status=NotificationStatus.STAGED,
            timezone="UTC"
        )

        notification_data = {
            'user_id': sample_notification.user_id,
            'message': sample_notification.message,
            'status': sample_notification.status.value,
            'timezone': sample_notification.timezone,
            'sent_at': sample_notification.sent_at,
            'error': sample_notification.error,
            'attempt_count': sample_notification.attempt_count,
            'last_attempt': sample_notification.last_attempt
        }

        # Setup mock returns
        cache._redis_mock.hkeys.return_value = [b"123"]
        cache._redis_mock.smembers.return_value = set()
        cache._redis_mock.hget.return_value = json.dumps(notification_data).encode()

        notifications, has_more = await cache.get_notifications_batch("UTC", 10)

        assert len(notifications) == 1
        assert "123" in notifications
        notification = notifications["123"]
        assert notification.user_id == sample_notification.user_id
        assert notification.message == sample_notification.message
        assert notification.status == sample_notification.status
        assert notification.timezone == sample_notification.timezone

    @pytest.mark.asyncio
    async def test_get_notifications_batch_empty(self, cache):
        """Test getting notifications when none are available"""
        cache._redis_mock.hkeys.return_value = []
        cache._redis_mock.smembers.return_value = set()

        notifications, has_more = await cache.get_notifications_batch("UTC", 10)
        assert len(notifications) == 0
        assert not has_more

    @pytest.mark.asyncio
    async def test_update_notification_batch(self, cache):
        """Test updating a batch of notifications"""
        sample_notification = DailyNotification(
            user_id="123",
            message="Test notification",
            status=NotificationStatus.STAGED,
            timezone="UTC"
        )

        await cache.update_notification_batch([sample_notification])
        assert cache._pipeline_mock.called
        assert cache._pipeline_mock.hset.called
        assert cache._pipeline_mock.execute.called

    @pytest.mark.asyncio
    async def test_update_notification_batch_empty(self, cache):
        """Test updating an empty notification batch"""
        await cache.update_notification_batch([])
        assert not cache._pipeline_mock.called

    @pytest.mark.asyncio
    async def test_cleanup_processing_set_empty(self, cache):
        """Test cleanup with empty processing set"""
        cache._redis_mock.smembers.return_value = set()
        await cache.cleanup_processing_set("UTC")
        assert cache._redis_mock.smembers.called

    @pytest.mark.asyncio
    async def test_cleanup_processing_set_with_completed(self, cache):
        """Test cleanup with completed notifications"""
        user_id = "123"
        notification_data = {
            'status': NotificationStatus.COMPLETED.value
        }

        cache._redis_mock.smembers.return_value = {user_id.encode()}
        cache._redis_mock.hget.return_value = json.dumps(notification_data).encode()

        await cache.cleanup_processing_set("UTC")
        assert cache._pipeline_mock.srem.called
        assert cache._pipeline_mock.called

    @pytest.mark.asyncio
    async def test_close_connection(self, cache):
        """Test connection cleanup"""
        await cache.close()
        assert cache._redis_mock.close.called