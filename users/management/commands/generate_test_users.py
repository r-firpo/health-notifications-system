# users/management/commands/generate_test_users.py
import random
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.db.models import Count
from faker import Faker
from users.models import User, NotificationPreference
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Generates test users with notification preferences'

    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=150,
            help='Number of users to generate'
        )

    def handle(self, *args, **options):
        fake = Faker()
        count = options['count']

        # US Timezones
        TIMEZONES = [
            'America/New_York',
            'America/Chicago',
            'America/Los_Angeles'
        ]

        # Health goals templates
        HEALTH_GOALS = [
            "Lose {} kg of weight",
            "Run {} km per week",
            "Exercise {} times per week",
            "Maintain a balanced diet and exercise {} times per week",
            "Build muscle and strength through {} weekly gym sessions",
            "Improve flexibility with {} yoga sessions per week"
        ]

        total_created = 0
        opt_in_count = min(100, count)  # First 100 users (or all if count < 100) will opt in

        try:
            with transaction.atomic():
                # Create users in batches of 20 for efficiency
                batch_size = 20
                for batch_start in range(0, count, batch_size):
                    batch_users = []
                    batch_prefs = []

                    batch_end = min(batch_start + batch_size, count)
                    logger.info(f"Generating users {batch_start + 1} to {batch_end}")

                    for i in range(batch_start, batch_end):
                        # Generate user data
                        signup_date = fake.date_between(
                            start_date='-1y',
                            end_date='today'
                        )

                        user = User(
                            name=fake.name(),
                            email=fake.unique.email(),
                            phone=fake.phone_number()[:15],
                            signup_date=signup_date,
                            age=random.randint(18, 80),
                            height_cm=random.randint(150, 200),
                            weight_kg=random.randint(45, 120),
                            activity_level=random.choice([
                                User.ActivityLevel.LOW,
                                User.ActivityLevel.MODERATE,
                                User.ActivityLevel.HIGH
                            ]).value,
                            health_goals=random.choice(HEALTH_GOALS).format(
                                random.randint(2, 10)
                            ),
                            timezone=random.choice(TIMEZONES)
                        )
                        batch_users.append(user)

                    # Bulk create users
                    created_batch = User.objects.bulk_create(batch_users)
                    total_created += len(created_batch)

                    # Create notification preferences
                    for user in created_batch:
                        is_opt_in = (total_created <= opt_in_count)  # First 100 users opt in
                        pref = NotificationPreference(
                            user=user,
                            opt_in=is_opt_in,
                            email_enabled=False,
                            sms_enabled=False,
                            push_enabled=is_opt_in  # Only opted-in users get push notifications
                        )
                        batch_prefs.append(pref)

                    # Bulk create preferences
                    NotificationPreference.objects.bulk_create(batch_prefs)
                    logger.info(f"Created {len(created_batch)} users with preferences")

            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully created {total_created} users with {opt_in_count} opted-in for notifications'
                )
            )

            # Log distribution statistics
            self.log_distribution_stats()

        except Exception as e:
            logger.error(f"Error generating users: {str(e)}")
            raise

    def log_distribution_stats(self):
        """Log distribution statistics of created users"""
        total_users = User.objects.count()

        # Timezone distribution
        timezone_stats = User.objects.values('timezone').annotate(count=Count('id'))
        logger.info("\nTimezone Distribution:")
        for stat in timezone_stats:
            count = stat['count']
            percentage = (count / total_users) * 100
            logger.info(f"{stat['timezone']}: {count} users ({percentage:.1f}%)")

        # Activity level distribution
        activity_stats = User.objects.values('activity_level').annotate(count=Count('id'))
        logger.info("\nActivity Level Distribution:")
        for stat in activity_stats:
            count = stat['count']
            percentage = (count / total_users) * 100
            logger.info(f"{stat['activity_level']}: {count} users ({percentage:.1f}%)")

        # Notification preferences
        opted_in = NotificationPreference.objects.filter(opt_in=True).count()
        logger.info("\nNotification Preferences:")
        logger.info(f"Opted-in users: {opted_in}")
        logger.info(f"Non-opted-in users: {total_users - opted_in}")