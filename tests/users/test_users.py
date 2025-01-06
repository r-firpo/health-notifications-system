import pytest
from rest_framework.test import APIClient
from rest_framework import status
from users.models import User, NotificationPreference


@pytest.mark.django_db
class TestUserViewSet:
    @pytest.fixture(autouse=True)
    def setup(self, user_data):
        self.client = APIClient()
        self.user_data = user_data

    @pytest.fixture
    def user_data(self):
        return {
            "name": "Test User",
            "email": "testuser@example.com",
            "phone": "1234567890",
            "signup_date": "2025-01-01",
            "age": 30,
            "height_cm": 170,
            "weight_kg": 70,
            "activity_level": "moderate",
            "health_goals": "Lose weight",
            "timezone": "UTC",
            "notification_preferences": {
                "opt_in": True,
                "email_enabled": True,
                "sms_enabled": False,
                "push_enabled": True,
            },
        }

    @pytest.fixture
    def create_user(self,user_data):
        user_data_copy = user_data.copy()
        notification_preferences_data = user_data_copy.pop("notification_preferences")
        user = User.objects.create(**user_data_copy)  # Create the User
        NotificationPreference.objects.create(user=user,
                                              **notification_preferences_data)
        return user

    def test_create_user_success(self):
        self.user_data["email"] = "newuser@example.com"
        response = self.client.post("/users/", self.user_data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert User.objects.count() == 1

    def test_create_user_duplicate_email(self, create_user):
        response = self.client.post("/users/", self.user_data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_user_success(self, create_user):
        response = self.client.get(f"/users/{create_user.id}/")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["email"] == create_user.email

    def test_retrieve_user_not_found(self):
        response = self.client.get("/users/999/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_user_success(self, create_user):
        data = {"name": "Updated Name"}
        response = self.client.patch(f"/users/{create_user.id}/", data, format="json")
        assert response.status_code == status.HTTP_200_OK
        create_user.refresh_from_db()
        assert create_user.name == "Updated Name"

    def test_update_user_validation_error(self, create_user):
        data = {"age": -10}  # Invalid age
        response = self.client.patch(f"/users/{create_user.id}/", data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_user_success(self, create_user):
        response = self.client.delete(f"/users/{create_user.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert User.objects.count() == 0

    def test_delete_user_not_found(self):
        response = self.client.delete("/users/999/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_users(self, create_user):
        response = self.client.get("/users/")
        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data
        assert len(response.data["results"]) == 1

    def test_invalid_timezone(self):
        self.user_data["email"] = "timezoneuser@example.com"
        self.user_data["timezone"] = "Invalid/Timezone"
        response = self.client.post("/users/", self.user_data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST