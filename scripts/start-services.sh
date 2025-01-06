#!/bin/bash

# Create the shared network if it doesn't exist
docker network create notification-network 2>/dev/null || true

# Start the main services
docker-compose up -d

# Wait for Redis to be healthy
echo "Waiting for Redis to be ready..."
while ! docker-compose exec redis redis-cli ping; do
    sleep 1
done

# Start the Kafka services
docker-compose -f docker-compose.kafka.yml up -d