# models.py
from django.db import models
from django.utils import timezone

class User(models.Model):
    class ActivityLevel(models.TextChoices):
        LOW = 'low', 'Low'
        MODERATE = 'moderate', 'Moderate'
        HIGH = 'high', 'High'

    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=15, null=True, blank=True)
    signup_date = models.DateField()
    age = models.IntegerField()
    height_cm = models.IntegerField()
    weight_kg = models.IntegerField()
    activity_level = models.CharField(
        max_length=50,
        choices=ActivityLevel.choices,
        default=ActivityLevel.MODERATE
    )
    health_goals = models.TextField()
    timezone = models.CharField(max_length=50)
    timezone_updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['timezone']),
        ]

class NotificationPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_preferences')
    opt_in = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=False)
    push_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'opt_in']),
        ]