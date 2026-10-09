#!/bin/bash
# LuxUPT entrypoint script
# Builds uvicorn command from environment variables

set -e

# Default values
HOST="${UVICORN_HOST:-0.0.0.0}"
PORT="${WEB_PORT:-8080}"
WORKERS="${UVICORN_WORKERS:-1}"

# Map LOG_LEVEL to uvicorn log level
case "${LOG_LEVEL:-INFO}" in
    DEBUG)   UVICORN_LOG_LEVEL="debug" ;;
    INFO)    UVICORN_LOG_LEVEL="info" ;;
    WARNING) UVICORN_LOG_LEVEL="warning" ;;
    ERROR)   UVICORN_LOG_LEVEL="error" ;;
    *)       UVICORN_LOG_LEVEL="info" ;;
esac

# Build base command
CMD="uvicorn app.web.main:app --host $HOST --port $PORT --log-level $UVICORN_LOG_LEVEL"

# Add reload for development (mount source code and set UVICORN_RELOAD=true)
if [ "${UVICORN_RELOAD:-false}" = "true" ]; then
    CMD="$CMD --reload --reload-dir /app/luxupt"
else
    # Production: use multiple workers if specified
    if [ "$WORKERS" -gt 1 ]; then
        CMD="$CMD --workers $WORKERS"
    fi
fi

echo "Starting LuxUPT: $CMD"
exec $CMD
