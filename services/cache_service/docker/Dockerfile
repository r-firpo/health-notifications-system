FROM python:3.11-slim

WORKDIR /health-notification-system/services/cache_service

# Copy only requirements first for better caching
COPY ../../requirements.txt .
RUN pip install -r requirements.txt

# Copy the cache service code
COPY . .

CMD ["python", "-u", "cache_service.py"]