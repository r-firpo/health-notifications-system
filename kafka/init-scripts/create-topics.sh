#!/bin/bash

# Wait for Kafka to be ready
sleep 10

# Create topics with proper configurations
kafka-topics --create --if-not-exists \
    --bootstrap-server kafka:29092 \
    --topic daily_notifications.push \
    --partitions 3 \
    --replication-factor 1

kafka-topics --create --if-not-exists \
    --bootstrap-server kafka:29092 \
    --topic daily_notifications.retry \
    --partitions 3 \
    --replication-factor 1

kafka-topics --create --if-not-exists \
    --bootstrap-server kafka:29092 \
    --topic daily_notifications.dead_letter \
    --partitions 3 \
    --replication-factor 1

## Add some common timezone topics
#for TZ in "America.New_York" "Europe.London" "Asia.Tokyo" "America.Los_Angeles" "America.Chicago"
#do
#    kafka-topics --create --if-not-exists \
#        --bootstrap-server kafka:29092 \
#        --topic "daily_notifications.push.$TZ" \
#        --partitions 3 \
#        --replication-factor 1
#done