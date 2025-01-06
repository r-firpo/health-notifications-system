import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from pytz import timezone
from users.models import User
from services.cache_service.DailyNotificationCache import DailyNotificationCache, DailyNotification
from services.cache_service.NotificationStatus import NotificationStatus
from users.management.daily_notification_builder import NotificationBuilder


@pytest.mark.asyncio
class TestNotificationBuilder:

    @pytest.fixture
    def mock_cache(self):
        """Fixture for a mocked DailyNotificationCache."""
        return MagicMock(spec=DailyNotificationCache)

    @pytest.fixture
    def notification_builder(self, mock_cache):
        """Fixture for NotificationBuilder instance."""
        return NotificationBuilder(notification_cache=mock_cache)

    def test_initialization(self, mock_cache, notification_builder):
        """Test that NotificationBuilder initializes correctly."""
        assert notification_builder.notification_cache == mock_cache
        assert notification_builder.metrics == {
            'users_processed': 0,
            'notifications_built': 0,
            'errors': 0,
            'start_time': None,
        }

    def test_get_date_for_timezone(self, notification_builder):
        """Test `_get_date_for_timezone` returns correct date for timezone."""
        tz = 'America/New_York'
        current_date = notification_builder._get_date_for_timezone(tz)
        assert current_date == datetime.now(timezone(tz)).date()

    @patch("users.models.User.objects")
    @pytest.mark.django_db
    async def test_get_users_batch(self, mock_objects, notification_builder):
        """Test `_get_users_batch` retrieves users."""
        # Mock user objects
        mock_users = [MagicMock(spec=User, id=1, timezone='UTC')]

        # Create the queryset chain
        mock_queryset = MagicMock()
        mock_queryset.select_related.return_value = mock_queryset
        mock_queryset.filter.return_value = mock_queryset
        mock_queryset.order_by.return_value = mock_queryset

        # Make slice return a list that can be wrapped by sync_to_async
        mock_queryset.__getitem__ = MagicMock(return_value=mock_users)
        #Note: Django sync_to_async is very hard to test! part of the reason i avoid it

        # Set up the objects manager
        mock_objects.select_related = MagicMock(return_value=mock_queryset)

        # Call the method
        users = await notification_builder._get_users_batch(0, 10)

        # Assertions
        assert users == mock_users
        mock_objects.select_related.assert_called_once_with('notification_preferences')
        mock_queryset.filter.assert_called_once_with(
            notification_preferences__opt_in=True,
            notification_preferences__push_enabled=True
        )
        mock_queryset.order_by.assert_called_once_with('timezone', 'id')

    @patch("services.llm.llm_service.HealthNotificationLLMService.get_instance")
    async def test_build_notification_payload(self, mock_llm_service, notification_builder):
        """Test `_build_notification_payload` generates notifications."""
        # Mock user and LLM service
        mock_user = MagicMock(spec=User, id=1, timezone='UTC', name="John", age=30,
                              activity_level="moderate", health_goals="Lose weight")
        # mock_llm_service.return_value.generate_health_notification = AsyncMock(
        #     return_value="your daily message"
        # ) #TODO this is how we would mock the message if the LLM service was running

        notifications = await notification_builder._build_notification_payload(mock_user)
        assert len(notifications) == 1
        assert notifications[0].user_id == "1"
        assert notifications[0].message == "your daily message"
        assert notifications[0].status == NotificationStatus.STAGED

    @patch("daily_notification_builder.NotificationBuilder._get_users_batch")
    @patch("daily_notification_builder.NotificationBuilder._build_notification_payload")
    async def test_process_all_timezones(self, mock_build_notification, mock_get_users, notification_builder):
        """Test `process_all_timezones` processes users and caches notifications."""
        # Mock user batches and notifications
        mock_user = MagicMock(spec=User, id=1, timezone='UTC')
        mock_get_users.side_effect = [[mock_user], []]  # First batch has users, second is empty
        mock_notification = MagicMock(spec=DailyNotification)
        mock_build_notification.return_value = [mock_notification]

        await notification_builder.process_all_timezones()

        # Assertions
        mock_get_users.assert_called()
        mock_build_notification.assert_called_with(mock_user)
        notification_builder.notification_cache.update_notification_batch.assert_called_with([mock_notification])
        assert notification_builder.metrics['users_processed'] == 1
        assert notification_builder.metrics['notifications_built'] == 1