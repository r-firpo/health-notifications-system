FROM health-notification-system-base-kafka-service

WORKDIR /home/appuser

CMD ["python", "-u", "/home/appuser/services/kafka_producers/daily_notification_producer.py"]
