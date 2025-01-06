FROM python:3.11-slim

# Create a non-root user
RUN useradd -m appuser

WORKDIR /home/appuser

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends netcat-traditional && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install -r requirements.txt

# Create app directory and set Python path
ENV PYTHONPATH=/home/appuser

# Copy the services directory and set permissions
COPY --chown=appuser:appuser services/ /home/appuser/services/

USER appuser