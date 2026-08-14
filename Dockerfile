# Multi-stage Dockerfile for luxupt using Poetry

# Stage 1: Build stage
FROM python:3.13-slim AS builder

# Install system dependencies needed for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
ENV POETRY_VERSION=1.7.1 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry==$POETRY_VERSION

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install dependencies
RUN poetry install --only main --no-root --no-directory

# Copy source code (app/ package at the repo root — fleet layout standard)
COPY app ./app
COPY README.md ./

# Install the project
RUN poetry install --only main

# Stage 2: Runtime stage
FROM python:3.13-slim

# Static environment variables (don't change between builds)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/luxupt

# Install runtime dependencies (cacheable - no ARGs yet)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash appuser

# Add build arguments AFTER static layers (these change each build).
# The real version (CalVer YYYY.0M.MICRO) is stamped by the Makefile from the VERSION file
# via --build-arg VERSION=$(VERSION); this default is only a bare-`docker build` fallback.
# Canonical build stamps (FLEET-BUILD-DEPLOY-STANDARD): VERSION, CREATED (RFC-3339 UTC),
# BUILD_COMMIT (short git SHA — same value as the OCI revision label AND the app's cache-bust token).
ARG VERSION="0000.00.0"
ARG CREATED="1970-01-01T00:00:00Z"
ARG BUILD_COMMIT="unknown"
ENV LUXUPT_VERSION=$VERSION \
    LUXUPT_BUILD_DATE=$CREATED \
    BUILD_COMMIT=$BUILD_COMMIT

# OCI provenance labels (repo.oci_image_labels) — portable keys; .created is RFC-3339 UTC.
LABEL org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.created="$CREATED" \
      org.opencontainers.image.revision="$BUILD_COMMIT" \
      org.opencontainers.image.source="https://github.com/luxardolabs/luxupt" \
      org.opencontainers.image.title="luxupt"

# Set working directory
WORKDIR /app/luxupt

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Copy the app/ package (imported as `app.*`; PYTHONPATH=/app/luxupt) + the entrypoint
COPY --from=builder --chown=appuser:appuser /app/app ./app
COPY --chown=appuser:appuser entrypoint.sh ./

# Create output directories
RUN mkdir -p output/images output/videos && \
    chown -R appuser:appuser output

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Add virtual environment to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Switch to non-root user
USER appuser

# Health check - uses the /health/live endpoint for liveness
# start-period allows time for database init and camera discovery
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import httpx; r = httpx.get('http://localhost:${WEB_PORT:-8080}/health/live', timeout=5); exit(0 if r.status_code == 200 else 1)"

# Default environment (can be overridden)
# WEB_PORT: Port to listen on (default 8080)
# UVICORN_RELOAD: Set to "true" for hot reload in dev (default false)
# LOGGING_LEVEL: DEBUG, INFO, WARNING, ERROR (default INFO)
ENV WEB_PORT=8080

# Run via entrypoint script which builds uvicorn command from env vars
# CLI commands available via: docker exec <container> python -m app.main <command>
ENTRYPOINT ["./entrypoint.sh"]
