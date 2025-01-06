#!/bin/sh

echo "Waiting for postgres..."
while ! nc -z $POSTGRES_HOST 5432; do
  sleep 0.1
done
echo "PostgreSQL started"

python manage.py migrate
python manage.py collectstatic --noinput

exec "$@"