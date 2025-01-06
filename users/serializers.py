# serializers.py
import pytz
from rest_framework import serializers
from .models import User, NotificationPreference


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ['opt_in', 'email_enabled', 'sms_enabled', 'push_enabled']


class UserSerializer(serializers.ModelSerializer):
    notification_preferences = NotificationPreferenceSerializer()

    class Meta:
        model = User
        fields = [
            'id', 'name', 'email', 'phone', 'signup_date', 'age',
            'height_cm', 'weight_kg', 'activity_level', 'health_goals',
            'timezone', 'notification_preferences'
        ]
        read_only_fields = ['timezone_updated_at', 'created_at', 'updated_at', 'id']

    def create(self, validated_data):
        # Extract nested data for notification_preferences
        notification_preferences_data = validated_data.pop('notification_preferences')

        # Create the User instance
        user = User.objects.create(**validated_data)

        # Create the NotificationPreference instance and associate it with the user
        NotificationPreference.objects.create(user=user, **notification_preferences_data)

        return user

    def validate_timezone(self, value):
        try:
            pytz.timezone(value)
        except pytz.exceptions.UnknownTimeZoneError:
            raise serializers.ValidationError("Invalid timezone provided")
        return value

    def validate_age(self, value):
        if value < 0 or value > 150:
            raise serializers.ValidationError("Age must be between 0 and 150")
        return value

    def validate_activity_level(self, value):
        if value not in [choice[0] for choice in User.ActivityLevel.choices]:
            raise serializers.ValidationError(
                f"Activity level must be one of: {', '.join([choice[0] for choice in User.ActivityLevel.choices])}"
            )
        return value

    def validate(self, data):
        # Custom validation for the entire object
        if 'height_cm' in data and 'weight_kg' in data:
            if data['height_cm'] <= 0 or data['weight_kg'] <= 0:
                raise serializers.ValidationError("Height and weight must be positive values")
        return data