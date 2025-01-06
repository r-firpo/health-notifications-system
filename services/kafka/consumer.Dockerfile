FROM health-notification-system-base-kafka-service

WORKDIR /home/appuser

CMD ["python", "-u", "/home/appuser/services/kafka_consumers/daily_notification_consumer.py"]