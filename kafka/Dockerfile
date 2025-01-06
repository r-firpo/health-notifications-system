FROM confluentinc/cp-kafka:7.5.3

COPY --chmod=755 init-scripts/create-topics.sh /usr/local/bin/create-topics.sh

COPY --chmod=755 init-scripts/healthcheck.sh /usr/local/bin/healthcheck.sh

CMD ["sh", "-c", "/etc/confluent/docker/run & sleep 10 && /usr/local/bin/create-topics.sh & wait"]