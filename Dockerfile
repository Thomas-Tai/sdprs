# SDPRS Central Server - Zeabur Production Image
FROM python:3.11-slim

# System deps for psycopg2, pillow, etc.
RUN apt-get update && apt-get install -y --no-install-recommends     libpq-dev gcc     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure crash tracebacks appear in Zeabur logs
ENV PYTHONUNBUFFERED=1

# Install Python dependencies first (layer cache)
COPY central_server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Storage + data directories
RUN mkdir -p /app/storage /app/data

# Zeabur injects PORT=8080 at runtime; EXPOSE must be a literal number
EXPOSE 8080

# AUTH-006 / DATA-024: --proxy-headers makes Starlette trust X-Forwarded-For so
# request.client.host is the REAL client IP, not Zeabur's edge proxy. Without it
# the login throttle keys every user to one proxy IP → one colleague's typos lock
# out the whole room. --forwarded-allow-ips="*" is safe here because the app is
# ONLY reachable through Zeabur's proxy (never bound to a public interface
# directly); tighten to the proxy CIDR if that ever changes.
CMD ["sh", "-c", "uvicorn central_server.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips=*"]
