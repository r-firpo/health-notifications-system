#!/bin/bash
kafka-topics --bootstrap-server localhost:9092 --list > /dev/null 2>&1
if [ $? -eq 0 ]; then
    # Check if our required topics exist
    TOPICS=$(kafka-topics --bootstrap-server localhost:9092 --list)
    if echo "$TOPICS" | grep -q "daily_notifications.push" && \
       echo "$TOPICS" | grep -q "daily_notifications.retry" && \
       echo "$TOPICS" | grep -q "daily_notifications.dead_letter"; then
        exit 0
    fi
fi
exit 1