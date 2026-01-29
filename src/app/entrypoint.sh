#!/bin/bash
# LuxUPT entrypoint script
# Builds uvicorn command from environment variables

set -e

# Default values
HOST="${UVICORN_HOST:-0.0.0.0}"
PORT="${WEB_PORT:-8080}"
WORKERS="${UVICORN_WORKERS:-1}"

# Map LOGGING_LEVEL to uvicorn log level
case "${LOGGING_LEVEL:-INFO}" in
    DEBUG)   LOG_LEVEL="debug" ;;
    INFO)    LOG_LEVEL="info" ;;
    WARNING) LOG_LEVEL="warning" ;;
    ERROR)   LOG_LEVEL="error" ;;
    *)       LOG_LEVEL="info" ;;
esac

# Build base command
CMD="uvicorn web.main:app --host $HOST --port $PORT --log-level $LOG_LEVEL"

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
