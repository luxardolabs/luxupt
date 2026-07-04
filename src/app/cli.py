# app/cli.py

"""CLI commands for the application."""

from datetime import datetime

from fetch_service import FetchService
from logging_config import get_logger
from startup import print_banner, print_configuration
from timelapse_service import TimelapseService
from web.main import start_web_server

logger = get_logger(__name__)


async def run_fetch_only() -> None:
    """Run only the fetch service."""
    fetch_service = FetchService()

    try:
        await fetch_service.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Fetch service error", extra={"error": str(e)})
    finally:
        await fetch_service.stop()


async def run_timelapse_only() -> None:
    """Run only the time-lapse service."""
    timelapse_service = TimelapseService()

    try:
        await timelapse_service.start()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Timelapse service error", extra={"error": str(e)})
    finally:
        await timelapse_service.stop()


async def run_web_only() -> None:
    """Run only the web interface."""
    try:
        await start_web_server()
    except ImportError as e:
        logger.error("Web interface dependencies not available", extra={"error": str(e)})
    except Exception as e:
        logger.error("Web server error", extra={"error": str(e)})


async def create_timelapse_now() -> None:
    """Create time-lapse videos immediately and exit."""
    timelapse_service = TimelapseService()

    try:
        await timelapse_service.create_timelapse_now()
        logger.info("Timelapse creation completed")
    except Exception as e:
        logger.error("Error creating timelapse", extra={"error": str(e)})


async def test_cameras() -> None:
    """Test camera connectivity by doing a one-off capture using FetchService."""
    fetch_service = FetchService()

    try:
        # Initialize the service (loads settings, cameras, etc.)
        await fetch_service.start()

        # Do a one-off capture at 60s interval
        timestamp = int(datetime.now().timestamp())
        results = await fetch_service.capture_once(timestamp, interval=60)

        # Report results
        successful = sum(1 for result in results.values() if result.success)
        total = len(results)
        camera_results = {name: "accessible" if result.success else "failed" for name, result in results.items()}

        logger.info(
            "Camera test completed",
            extra={
                "successful": successful,
                "total": total,
                "cameras": camera_results,
            },
        )

    except Exception as e:
        logger.error("Error testing cameras", extra={"error": str(e)})
    finally:
        await fetch_service.stop()


def show_help() -> None:
    """Show CLI help message."""
    logger.info(
        "CLI usage",
        extra={
            "application": "LuxUPT",
            "normal_operation": "uvicorn web.main:app --host 0.0.0.0 --port 8080",
            "commands": {
                "test": "Test camera connectivity and exit",
                "create": "Create time-lapse videos now and exit",
                "fetch": "Run only the image fetch service",
                "timelapse": "Run only the time-lapse creation service",
                "web": "Run only the web interface (dev)",
                "help": "Show this help message",
            },
        },
    )


async def handle_cli_command(command: str) -> bool:
    """
    Handle CLI command.

    Returns True if a command was handled (and main should exit),
    False if no command matched (continue to normal startup).
    """
    if command == "test":
        print_banner()
        logger.info("Running camera connectivity test")
        await test_cameras()
        return True

    elif command == "create":
        print_banner()
        logger.info("Creating timelapse videos now")
        await create_timelapse_now()
        return True

    elif command in ["fetch", "capture"]:
        print_banner()
        print_configuration()
        logger.info("Starting fetch service only")
        await run_fetch_only()
        return True

    elif command in ["timelapse", "video"]:
        print_banner()
        print_configuration()
        logger.info("Starting timelapse service only")
        await run_timelapse_only()
        return True

    elif command == "web":
        print_banner()
        print_configuration()
        logger.info("Starting web interface only")
        await run_web_only()
        return True

    elif command in ["help", "-h", "--help"]:
        show_help()
        return True

    else:
        logger.error("Unknown command", extra={"command": command, "hint": "Use 'python main.py help'"})
        return True

    return False
