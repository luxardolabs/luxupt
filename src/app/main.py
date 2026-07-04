# app/main.py

"""CLI entry point for LuxUPT.

For normal operation, use uvicorn directly:
    uvicorn web.main:app --host 0.0.0.0 --port 8080

This module provides CLI commands for testing and maintenance:
    python main.py test      - Test camera connectivity
    python main.py validate  - Validate system capacity
    python main.py create    - Create time-lapses now
    python main.py fetch     - Run fetch service only
    python main.py timelapse - Run timelapse service only
    python main.py help      - Show help
"""

import asyncio
import sys

from cli import handle_cli_command, show_help
from logging_config import get_logger, setup_logging

logger = get_logger(__name__)


async def main() -> None:
    """CLI entry point."""
    setup_logging()

    # Must have a command
    if len(sys.argv) < 2:
        show_help()
        logger.info("For normal operation, run: uvicorn web.main:app --host 0.0.0.0 --port 8080")
        return

    command = sys.argv[1].lower()
    await handle_cli_command(command)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Unexpected error", extra={"error": str(e)})
        sys.exit(1)
