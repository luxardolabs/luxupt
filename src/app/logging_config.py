"""
Structured logging configuration with JSON/plain text support.

Provides:
- Configurable output format (JSON or plain text)
- Structured logging via extra={} fields
- Per-module log level overrides
- Consistent formatting across all application components
"""

import logging
import sys
from typing import Any

import config

# Optional JSON logging support
try:
    from pythonjsonlogger import jsonlogger

    JSON_LOGGING_AVAILABLE = True
    _JsonFormatterBase: type = jsonlogger.JsonFormatter
except ImportError:
    JSON_LOGGING_AVAILABLE = False
    _JsonFormatterBase = logging.Formatter


class StructuredJsonFormatter(_JsonFormatterBase):
    """
    Custom JSON formatter that includes additional context fields.

    All extra={} fields passed to logging calls are included in the JSON output.
    """

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Add custom fields to log record."""
        super().add_fields(log_record, record, message_dict)

        # Standard fields
        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["module"] = record.module
        log_record["function"] = record.funcName
        log_record["line"] = record.lineno

        # Application context
        log_record["service"] = "luxupt"

        # Thread info (useful for debugging async issues)
        log_record["thread"] = record.thread
        log_record["thread_name"] = record.threadName

        # Task name for async (Python 3.12+)
        if hasattr(record, "taskName"):
            log_record["task_name"] = record.taskName

        # Exception details (if present)
        if record.exc_info:
            log_record["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info) if record.exc_info else None,
            }


class StructuredTextFormatter(logging.Formatter):
    """
    Plain text formatter that includes extra fields in a readable format.

    Extra fields are appended to the message as key=value pairs.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with extra fields."""
        # Get the base formatted message
        message = super().format(record)

        # Append extra fields if present
        # Filter out standard LogRecord attributes to get only custom extras
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "exc_info",
            "exc_text",
            "thread",
            "threadName",
            "taskName",
            "message",
        }

        extra_fields = {k: v for k, v in record.__dict__.items() if k not in standard_attrs and not k.startswith("_")}

        if extra_fields:
            extras_str = " ".join(f"{k}={v}" for k, v in extra_fields.items())
            message = f"{message} | {extras_str}"

        return message


def _parse_log_level(level_str: str) -> int:
    """Parse log level string to logging constant."""
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return level_map.get(level_str.upper(), logging.INFO)


def setup_logging() -> None:
    """
    Configure application-wide structured logging.

    Environment variables:
        LOGGING_LEVEL: Global log level (DEBUG, INFO, WARNING, ERROR)
        LOGGING_FORMAT: Output format ("json" or "text", default: "text")
        LOGGING_MODULE_LEVELS: JSON dict of module -> level overrides
            Example: '{"camera_manager": "DEBUG", "fetch_service": "WARNING"}'
    """
    log_level = _parse_log_level(config.LOGGING_LEVEL)
    use_json = config.LOGGING_FORMAT.lower() == "json"

    # Create appropriate formatter
    if use_json:
        if not JSON_LOGGING_AVAILABLE:
            logging.warning(
                "JSON logging requested but pythonjsonlogger not installed. Falling back to text format. "
                "Install with: pip install python-json-logger"
            )
            use_json = False

    formatter: logging.Formatter
    if use_json:
        formatter = StructuredJsonFormatter(
            fmt="%(timestamp)s %(level)s %(logger)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    else:
        formatter = StructuredTextFormatter(
            fmt="%(asctime)s - %(levelname)s - %(name)s - %(funcName)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)  # Handler accepts all, loggers filter
    console_handler.setFormatter(formatter)

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()  # Remove any existing handlers
    root_logger.addHandler(console_handler)

    # =================================================================
    # Third-party library noise reduction
    # =================================================================
    third_party_defaults = {
        "uvicorn": logging.WARNING,
        "uvicorn.access": logging.WARNING,
        "uvicorn.error": logging.INFO,
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "aiosqlite": logging.WARNING,
        "sqlalchemy": logging.WARNING,
        "sqlalchemy.engine": logging.WARNING,
        "sqlalchemy.engine.Engine": logging.WARNING,
        "sqlalchemy.pool": logging.WARNING,
        "asyncio": logging.WARNING,
        "PIL": logging.WARNING,
    }

    for module, level in third_party_defaults.items():
        logging.getLogger(module).setLevel(level)

    # =================================================================
    # Application module defaults (use global level)
    # =================================================================
    app_modules = [
        "camera_manager",
        "fetch_service",
        "timelapse_service",
        "config",
        "crud",
        "services",
        "web",
        "db",
    ]
    for module in app_modules:
        logging.getLogger(module).setLevel(log_level)

    # =================================================================
    # Per-module overrides from configuration
    # =================================================================
    module_overrides = config.LOGGING_MODULE_LEVELS
    if module_overrides:
        for module, level_str in module_overrides.items():
            level = _parse_log_level(level_str)
            logging.getLogger(module).setLevel(level)

    # Log initialization (use structured logging from the start)
    logger = get_logger(__name__)
    logger.info(
        "Logging initialized",
        extra={
            "log_level": logging.getLevelName(log_level),
            "format": "json" if use_json else "text",
            "module_overrides": list(module_overrides.keys()) if module_overrides else [],
        },
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with structured logging support.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("Snapshot captured", extra={"camera": "Front Door", "interval": 60, "file_size": 12345})
        logger.error("Capture failed", extra={"camera": "Back Yard", "error": "timeout"}, exc_info=True)
    """
    return logging.getLogger(name)
