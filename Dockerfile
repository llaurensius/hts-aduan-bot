FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Jakarta

# Install system dependencies (tzdata for Asia/Jakarta time, curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY backup/ ./backup/

# Create directories for persistent data and logs
RUN mkdir -p data/backups logs

# Default ports:
# 8888: Web Dashboard
# 8080: Internal Health Endpoint
EXPOSE 8888 8080

# Default command (bisa dioverride di docker-compose)
CMD ["python", "-m", "app.main"]
