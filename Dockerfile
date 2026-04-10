FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Create log directory
RUN mkdir -p /app/logs

# Non-root user
RUN useradd -m sdr && chown -R sdr:sdr /app
USER sdr

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/webhooks/health').raise_for_status()"

# Default: server mode
CMD ["python", "main.py", "serve", "--host", "0.0.0.0", "--port", "8000"]
