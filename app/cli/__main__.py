# luxarch:admin-bootstrap-cli asset v3 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit admin-bootstrap-cli`.
"""The two non-request owners of the admin bootstrap: the startup hook and the recovery CLI.

`luxarch --emit admin-bootstrap-cli`. Drop this in at `app/cli.py`. The standard is
`luxarch --doc FLEET-ADMIN-BOOTSTRAP-STANDARD`.

    docker compose exec app python -m app.cli reset-password <login>

prints a random temporary password ONCE and sets `must_change_password`, so the owner signs in with
it and is sent straight to the change form. Being able to run a command inside the container is the
proof of ownership; no email is involved, because a self-hosted box often has no mail at all.

The reset is on record where the owner looks later: the command configures the fleet logging
(`luxarch --emit logging`) and sends its lines to the container's log, so `docker logs` (or
`docker compose logs`) shows the `password_reset_to_temporary` line with the login it reset, and no
password or hash. A `docker exec` command is not the container's main process, so its own output
reaches only the terminal that ran it; the container's log is PID 1's output. Where that cannot be
opened (off a container, or as a user who cannot open PID 1's output) the line stays on this
command's output and stderr says the container's log has no record.

`bootstrap_on_startup()` is what the app's lifespan awaits. It is a separate transaction owner (it
runs before any request), like the CLI, so list `app/cli.py` in `[allowlist].non_request_files`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# --- EDIT THIS: your logging module (`luxarch --emit logging`), non-request session owner and the
# core service (fleet homes shown).
from app.core import config
from app.core.logging_config import configure_logging
from app.db.database import get_db_context
from app.services.core.admin_bootstrap_core_service import (
    AdminBootstrapCoreService,
    UnknownUserError,
)


# --- EDIT THIS: the logging setup your app's entrypoint runs, with the same arguments (service,
# version, environment, redactor), so the reset's line reads like every other line the app writes.
def _configure_logging() -> None:
    configure_logging(
        service=config.SERVICE_NAME,
        version=config.BUILD_VERSION,
        levels=config.LOG_LEVELS,
    )


# What `docker logs` shows: the output of the container's main process.
CONTAINER_LOG = Path("/proc/1/fd/1")


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _own_output() -> os.stat_result | None:
    try:
        return os.fstat(1)
    except OSError:
        return None


def _log_to_the_container() -> None:
    """Configure the fleet logging, then point its handler at the container's log, unless this
    process already writes there (it is the main process) or that output is discarded."""
    _configure_logging()
    try:
        fd = os.open(CONTAINER_LOG, os.O_WRONLY | os.O_APPEND)
    except OSError as exc:
        sys.stderr.write(
            f"the container's log ({CONTAINER_LOG}) cannot be opened ({exc.strerror}): this "
            "reset is logged only in this command's output\n"
        )
        return
    target = os.fstat(fd)
    own = _own_output()
    if (own is not None and _same_file(target, own)) or _same_file(
        target, os.stat(os.devnull)
    ):
        os.close(fd)
        return
    stream = os.fdopen(fd, "w", encoding="utf-8")
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(stream)


async def bootstrap_on_startup() -> None:
    """Seed the first admin or open `/setup`. Await it from the app's lifespan."""
    async with get_db_context() as db:
        await AdminBootstrapCoreService().bootstrap(db)


async def _reset_password(login: str) -> int:
    try:
        async with get_db_context() as db:
            temporary = await AdminBootstrapCoreService().reset_password(db, login)
    except UnknownUserError:
        sys.stderr.write(f"no user signs in as {login!r}; nothing changed\n")
        return 1
    # Printed only after the transaction committed: a password shown for a change that rolled back
    # would lock the owner out a second time.
    sys.stdout.write(
        f"Temporary password for {login}: {temporary}\n"
        "It is shown once. They must change it when they next sign in.\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser(
        "reset-password",
        help="give a user a one-time temporary password they must change at next sign-in",
    )
    reset.add_argument(
        "login",
        help="what the user signs in with (their email or username, per LOGIN_KIND)",
    )
    args = parser.parse_args(argv)
    login: str = args.login
    _log_to_the_container()
    return asyncio.run(_reset_password(login))


if __name__ == "__main__":
    raise SystemExit(main())
