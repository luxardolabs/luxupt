"""Data migrations commit through the app's real (async) alembic path.

`c4e7b1a90f22` rewrites stored naive-local timestamps as UTC. If its UPDATEs were stamped but not
committed, that would be permanent: nothing re-runs a migration the version table says is applied.
This locks the path the app uses (aiosqlite, env.py's run_sync): build a database at the revision
before the shift, write one naive local timestamp, upgrade to head, read it back on a separate
connection. Written while rehearsing the 1.1.5 -> 2026.10.1 prod upgrade on a copy of prod data.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

APP_DIR = Path(__file__).resolve().parents[2] / "app"
BEFORE_SHIFT = "9f3d0d41053d"


def _config(url: str) -> Config:
    cfg = Config(str(APP_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(APP_DIR / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_timestamp_shift_is_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "timelapse.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(cfg, BEFORE_SHIFT)

    # closing(): sqlite3's own context manager commits but never closes the connection.
    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.execute(
            "INSERT INTO activities (timestamp, activity_type, message) "
            "VALUES ('2026-10-09 00:33:36.220867', 'capture_success', 'written by 1.1.5')"
        )

    # The deployment's zone: the stored value is Chicago wall-clock (CDT, UTC-5 on that date).
    monkeypatch.setenv("DISPLAY_TIMEZONE", "America/Chicago")
    command.upgrade(cfg, "head")

    with closing(sqlite3.connect(db_file)) as reader:
        (stored,) = reader.execute("SELECT timestamp FROM activities").fetchone()
        (version,) = reader.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version != BEFORE_SHIFT
    assert stored == "2026-10-09 05:33:36.220867", "shift stamped but not committed"
