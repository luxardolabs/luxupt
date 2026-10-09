# Multi-stage Dockerfile for luxupt using Poetry

# Stage 1: Build stage
FROM python:3.14-slim AS builder

# Install system dependencies needed for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
ENV POETRY_VERSION=2.4.1 \
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

# Copy source code (app/ package at the repo root — fleet layout standard).
# package-mode=false: no root wheel is built; the app runs from this source on PYTHONPATH.
COPY app ./app
COPY README.md ./

# Dev dependency group for the TEST stage only (pytest, coverage): it never reaches production,
# which copies its venv from `builder`.
FROM builder AS builder-dev
RUN poetry install --with dev --no-root --no-directory

# luxarch:css-stage asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit css-stage`.
# ---- css: compile the stylesheet; only its output reaches the app image (luxarch --doc FLEET-BUILD-DEPLOY-STANDARD) ----
# Paste above your app stage, then copy ONLY the output into it:
#   COPY --from=css /build/app/static/css/app.css /app/app/static/css/app.css
# Node, node_modules and the Tailwind toolchain never reach the runtime image. In a monorepo the paths
# are relative to the app's build context (apps/backend/).
FROM node:24-slim AS css
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build:css
# ---- end css stage ----

# Stage 2: the runtime app — everything production ships. `test` and `production` both build FROM
# it, so the suite runs on exactly the layers that ship, and `production` stays the LAST stage: a
# bare `docker build` produces the deployable, never the dev-tooled test image.
FROM python:3.14-slim AS app

# Static environment variables (don't change between builds)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/luxupt

# Install runtime dependencies (cacheable - no ARGs yet). `apt-get upgrade` pulls the base
# image's OS packages (openssl, libssl, pcre2, ...) up to their fixed versions: the base tag lags
# its own security updates, and luxaudit's image leg reds every fixable HIGH/CRITICAL it ships.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash appuser

# Add build arguments AFTER static layers (these change each build).
# The real version (CalVer YYYY.0M.MICRO) is stamped by the Makefile from the VERSION file
# via --build-arg VERSION=$(VERSION); this default is only a bare-`docker build` fallback.
# Canonical provenance build args (repo.dockerfile_provenance_args, FLEET-BUILD-DEPLOY-STANDARD):
# BUILD_VERSION (CalVer), BUILD_TIMESTAMP (RFC-3339 UTC), BUILD_COMMIT (short git SHA — the same
# value as the OCI revision label AND the app's cache-bust token, read from env as BUILD_COMMIT).
ARG BUILD_VERSION="0000.00.0"
ARG BUILD_TIMESTAMP="1970-01-01T00:00:00Z"
ARG BUILD_COMMIT="unknown"
ENV BUILD_VERSION=$BUILD_VERSION \
    BUILD_TIMESTAMP=$BUILD_TIMESTAMP \
    BUILD_COMMIT=$BUILD_COMMIT

# OCI provenance labels (repo.oci_image_labels) — portable keys; .created is RFC-3339 UTC.
LABEL org.opencontainers.image.version="$BUILD_VERSION" \
      org.opencontainers.image.created="$BUILD_TIMESTAMP" \
      org.opencontainers.image.revision="$BUILD_COMMIT" \
      org.opencontainers.image.source="https://github.com/luxardolabs/luxupt" \
      org.opencontainers.image.title="luxupt"

# Set working directory
WORKDIR /app/luxupt

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Copy the app/ package (imported as `app.*`; PYTHONPATH=/app/luxupt) + the entrypoint
COPY --from=builder --chown=appuser:appuser /app/app ./app
# The stylesheet comes ONLY from the css stage, after the app copy, so nothing from the build
# context can overwrite it (fw.generated_assets_built_in_image).
COPY --from=css --chown=appuser:appuser /build/app/static/css/app.css ./app/static/css/app.css
COPY --chown=appuser:appuser entrypoint.sh ./

# Create output directories
RUN mkdir -p output/images output/videos && \
    chown -R appuser:appuser output

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Nothing runs pip at runtime, and pip vendors its own urllib3/msgpack/setuptools that no lock
# bump reaches (luxaudit --doc REFERENCE, image leg) -- so the runtime carries none of it.
RUN /app/.venv/bin/python -m pip uninstall -y pip && python -m pip uninstall -y pip

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
# LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default INFO)
ENV WEB_PORT=8080

# Run via entrypoint script which builds uvicorn command from env vars
# CLI commands available via: docker exec <container> python -m app.main <command>
ENTRYPOINT ["./entrypoint.sh"]

# Stage 3: the TEST image (`make test-build`, luxarch test-block): the app layers production ships
# plus the dev group's venv. Never pushed, never a deploy tag. The suite runs against the source
# mounted at /repo, so PYTHONPATH is cleared: imports resolve from the mount, not the baked copy.
FROM app AS test
COPY --from=builder-dev --chown=appuser:appuser /app/.venv /app/.venv
ENV PYTHONPATH=
# The suite's command is given by `make test`; production's uvicorn entrypoint would swallow it.
ENTRYPOINT []

# Stage 4: what ships — the app stage, unchanged. Deploy builds name it (`--target production`).
FROM app AS production
