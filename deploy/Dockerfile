# SDPRS Central Server Dockerfile
# Multi-stage build for production deployment
#
# Build: docker build -t sdprs-server -f deploy/Dockerfile .
# Run:   docker run -p 8000:8000 --env-file .env sdprs-server

FROM python:3.11-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libopenjp2-7 \
    libtiff6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first (cache layer)
COPY central_server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY central_server/ /app/central_server/
COPY shared/ /app/shared/

# Create necessary directories
RUN mkdir -p /app/data /app/storage/events

# Environment defaults
ENV DB_PATH=/app/data/sdprs.db \
    STORAGE_PATH=/app/storage \
    STORAGE_DIR=/app/storage \
    MQTT_BROKER=mosquitto \
    MQTT_PORT=1883 \
    RETENTION_DAYS=30 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Run with uvicorn
CMD ["uvicorn", "central_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
