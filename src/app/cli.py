# app/cli.py

"""CLI commands for the application."""

import sys
from datetime import datetime

from crud.fetch_settings_crud import CRUDFetchSettings
from db.connection import async_session
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


async def set_protect_creds(username: str, password: str) -> None:
    """Store UniFi Protect username/password in fetch_settings.

    Used to set the credentials required for private-API endpoints
    (recording-snapshot, video/export). The Setup UI does the same thing.
    """
    crud = CRUDFetchSettings()
    async with async_session() as db:
        await crud.update_settings(db, obj_in={"username": username, "password": password})
        await db.commit()
    logger.info(
        "Stored Protect credentials in fetch_settings",
        extra={"username": username, "password_len": len(password)},
    )


def _parse_kv_args(argv: list[str]) -> dict[str, str]:
    """Parse --key=value or --key value pairs from argv. Stops at first non-flag."""
    out: dict[str, str] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if not arg.startswith("--"):
            i += 1
            continue
        if "=" in arg:
            key, value = arg[2:].split("=", 1)
            out[key] = value
            i += 1
        else:
            key = arg[2:]
            if i + 1 >= len(argv):
                raise ValueError(f"flag --{key} expects a value")
            out[key] = argv[i + 1]
            i += 2
    return out


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
                "set-protect-creds": "Store Protect username/password (--username X --password Y)",
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

    elif command == "set-protect-creds":
        try:
            kv = _parse_kv_args(sys.argv[2:])
        except ValueError as e:
            logger.error("Argument parse error", extra={"error": str(e)})
            return True
        username = kv.get("username")
        password = kv.get("password")
        if not username or not password:
            logger.error(
                "set-protect-creds requires --username and --password",
                extra={"example": "python main.py set-protect-creds --username admin --password 'secret'"},
            )
            return True
        await set_protect_creds(username, password)
        return True

    elif command in ["help", "-h", "--help"]:
        show_help()
        return True

    else:
        logger.error("Unknown command", extra={"command": command, "hint": "Use 'python main.py help'"})
        return True

    return False
