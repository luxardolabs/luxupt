"""Data migrations commit through the app's real (async) alembic path.

`c4e7b1a90f22` rewrites stored naive-local timestamps as UTC. If its UPDATEs were stamped but not
committed, that would be permanent: nothing re-runs a migration the version table says is applied.
This locks the path the app uses (aiosqlite, env.py's run_sync): build a database at the revision
before the shift, write one naive local timestamp, upgrade to head, read it back on a separate
connection. Written while rehearsing the 1.1.5 -> 2026.10.1 prod upgrade on a copy of prod data.
"""

import json
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


BEFORE_ADMIN_BOOTSTRAP = "c4e7b1a90f22"


def test_admin_bootstrap_migration_round_trips_the_env_account(tmp_path: Path) -> None:
    # 5b8e2d71c4a9 drops auth_source. A downgrade that re-added it as 'database' everywhere left
    # the previous release unable to find its env account: its startup inserted WEB_USERNAME
    # again and failed on the unique username, so a rollback could not start.
    db_file = tmp_path / "timelapse.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(cfg, BEFORE_ADMIN_BOOTSTRAP)
    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, auth_source) "
            "VALUES ('admin', 'ENV_MANAGED', 1, 'env'), ('db-user', '$argon2id$x', 0, 'database')"
        )

    command.upgrade(cfg, "head")
    with closing(sqlite3.connect(db_file)) as reader:
        upgraded = dict(
            reader.execute("SELECT username, must_change_password FROM users")
        )
    assert upgraded == {"admin": 1, "db-user": 0}

    command.downgrade(cfg, BEFORE_ADMIN_BOOTSTRAP)
    with closing(sqlite3.connect(db_file)) as reader:
        sources = dict(reader.execute("SELECT username, auth_source FROM users"))
    assert sources == {"admin": "env", "db-user": "database"}


BEFORE_RANDOM_VERSIONS = "5b8e2d71c4a9"


def test_existing_users_get_distinct_session_versions(tmp_path: Path) -> None:
    db_file = tmp_path / "timelapse.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(cfg, BEFORE_RANDOM_VERSIONS)
    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) "
            "VALUES ('one', '$argon2id$x', 1), ('two', '$argon2id$x', 0)"
        )

    command.upgrade(cfg, "head")
    with closing(sqlite3.connect(db_file)) as reader:
        versions = dict(reader.execute("SELECT username, session_version FROM users"))
    assert versions["one"] != versions["two"]
    assert 0 not in versions.values()

    command.downgrade(cfg, BEFORE_RANDOM_VERSIONS)
    with closing(sqlite3.connect(db_file)) as reader:
        after_downgrade = dict(
            reader.execute("SELECT username, session_version FROM users")
        )
    assert after_downgrade == versions


BEFORE_STREAM_REDACTION = "7c1e4f9a2b3d"
STORED_STDERR = (
    "FFmpeg error: [tls @ 0x5] Failed to resolve hostname nvr.invalid: Name not known\n"
    "rtsps://nvr.invalid:7441/aBcDeFgH?enableSrtp: Connection refused"
)


def test_stored_capture_errors_lose_the_stream_address(tmp_path: Path) -> None:
    db_file = tmp_path / "timelapse.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(cfg, BEFORE_STREAM_REDACTION)
    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.execute(
            "INSERT INTO captures (camera_id, camera_safe_name, timestamp, capture_datetime, "
            "capture_date, interval, status, capture_method, error_message) VALUES "
            "('c', 'c', 1, '2026-01-01 00:00:00', '2026-01-01', 60, 'failed', 'rtsp', ?), "
            "('c', 'c', 2, '2026-01-01 00:00:00', '2026-01-01', 60, 'failed', 'api', "
            "'Network error')",
            (STORED_STDERR,),
        )
        conn.execute(
            "INSERT INTO activities (timestamp, activity_type, message, details) VALUES "
            "('2026-01-01 00:00:00', 'capture_failed', ?, ?), "
            "('2026-01-01 00:00:00', 'capture_success', 'See https://docs.invalid/x', NULL)",
            (
                f"Failed to capture from c: {STORED_STDERR}",
                json.dumps({"error": STORED_STDERR}),
            ),
        )

    command.upgrade(cfg, "head")
    with closing(sqlite3.connect(db_file)) as reader:
        errors = [
            row[0]
            for row in reader.execute(
                "SELECT error_message FROM captures ORDER BY timestamp"
            )
        ]
        activities = reader.execute(
            "SELECT message, details FROM activities ORDER BY id"
        ).fetchall()

    assert "nvr.invalid" not in errors[0]
    assert "aBcDeFgH" not in errors[0]
    assert "Connection refused" in errors[0]
    assert errors[1] == "Network error"
    assert "nvr.invalid" not in activities[0][0]
    assert "aBcDeFgH" not in activities[0][0]
    assert "aBcDeFgH" not in json.loads(activities[0][1])["error"]
    assert "Connection refused" in json.loads(activities[0][1])["error"]
    assert activities[1][0] == "See https://docs.invalid/x"


def test_a_clean_activity_row_is_left_exactly_as_it_was(tmp_path: Path) -> None:
    db_file = tmp_path / "timelapse.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(cfg, BEFORE_STREAM_REDACTION)
    details = '{"error" : "FFmpeg error: Conversion failed"}'
    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.execute(
            "INSERT INTO activities (timestamp, activity_type, message, details) VALUES "
            "('2026-01-01 00:00:00', 'capture_failed', 'FFmpeg error: Conversion failed', ?)",
            (details,),
        )

    command.upgrade(cfg, "head")

    with closing(sqlite3.connect(db_file)) as reader:
        (stored,) = reader.execute("SELECT details FROM activities").fetchone()
    assert stored == details


@pytest.mark.parametrize("details", ["{not json", 3, "[" * 200_000])
def test_bad_activity_details_do_not_stop_the_upgrade(
    tmp_path: Path, details: object
) -> None:
    db_file = tmp_path / "timelapse.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(cfg, BEFORE_STREAM_REDACTION)
    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.execute(
            "INSERT INTO activities (timestamp, activity_type, message, details) VALUES "
            "('2026-01-01 00:00:00', 'capture_failed', ?, ?)",
            (STORED_STDERR, details),
        )

    command.upgrade(cfg, "head")

    with closing(sqlite3.connect(db_file)) as reader:
        (message, stored) = reader.execute(
            "SELECT message, details FROM activities"
        ).fetchone()
    assert "aBcDeFgH" not in message
    assert stored is None


def test_more_rows_than_one_batch_are_all_cleaned(tmp_path: Path) -> None:
    db_file = tmp_path / "timelapse.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(cfg, BEFORE_STREAM_REDACTION)
    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.executemany(
            "INSERT INTO activities (timestamp, activity_type, message) VALUES "
            "('2026-01-01 00:00:00', 'capture_failed', ?)",
            [(STORED_STDERR,)] * 1201,
        )

    command.upgrade(cfg, "head")

    with closing(sqlite3.connect(db_file)) as reader:
        (left,) = reader.execute(
            "SELECT COUNT(*) FROM activities WHERE message LIKE '%aBcDeFgH%'"
        ).fetchone()
    assert left == 0
