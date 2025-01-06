FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-client \
        netcat-traditional \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /health-notification-system

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy project files
COPY . .

# Add the project directory to PYTHONPATH
ENV PYTHONPATH="/health_notification_system"

# Create directory for static files
RUN mkdir -p staticfiles

# Make sure the entrypoint script is executable
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "wsgi:application"]