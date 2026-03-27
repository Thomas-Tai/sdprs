# SDPRS Central Server - Zeabur Production Image
FROM python:3.11-slim

# System deps for psycopg2, pillow, etc.
RUN apt-get update && apt-get install -y --no-install-recommends     libpq-dev gcc     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY central_server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Storage directory for MP4 uploads
RUN mkdir -p /app/storage

# Zeabur injects $PORT at runtime; default 8000 for local testing
ENV PORT=8000

EXPOSE $PORT

CMD ["sh", "-c", "uvicorn central_server.main:app --host 0.0.0.0 --port $PORT"]
