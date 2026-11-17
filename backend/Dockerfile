# Build context: Main-Project (includes the Epic modules outside backend/).
FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY backend/key-generation/requirements.txt ./requirements.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt 'gunicorn==23.0.0'

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PORT=8080 GRANT_DATA_DIR=/tmp/medilink
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels \
    && useradd --create-home --uid 10001 medilink
WORKDIR /app/backend/key-generation
COPY backend/key-generation/*.py ./
COPY backend/key-generation/testContractDeployment/artifacts/contracts/AccessRegistry.sol/AccessRegistry.json ./testContractDeployment/artifacts/contracts/AccessRegistry.sol/AccessRegistry.json
COPY subnet-template/epic_*.py /app/subnet-template/
COPY subnet-template/ehr_storage.py /app/subnet-template/
USER medilink
EXPOSE 8080
# One process: pending Epic handoffs and vault URL tokens are process-local.
CMD exec gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 1 --threads 8 --timeout 120 --error-logfile - 'cloud_app:create_cloud_app()'
