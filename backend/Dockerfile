# Use a lightweight, official Python base image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies if required, then clear cache
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY key-generation/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir 'gunicorn>=23,<24'

# Copy the rest of your application code
COPY key-generation/ ./key-generation/
WORKDIR /app/key-generation
ENV GRANT_DATA_DIR=/tmp/medilink

# Cloud Run injects a dynamic $PORT environment variable. 
# We bind Uvicorn/Gunicorn to 0.0.0.0 and use that dynamic port.
CMD exec gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 1 --threads 8 --timeout 120 'cloud_app:create_cloud_app()'
