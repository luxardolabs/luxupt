"""A migration that fails stops startup: init_db never boots the app on a half-built schema.

A database whose tables exist but whose `alembic_version` row is gone makes the baseline
migration fail with "table ... already exists". Booting past that would leave the schema
unversioned and skip the settings seed, so every later page and login reads a schema the
migration chain never finished.
"""

import os
import subprocess
import sys
from pathlib import Path

_INIT_DB = """
import asyncio
from app.db.database import init_db

asyncio.run(init_db())
"""

_FORGET_VERSION_THEN_INIT_DB = """
import asyncio
import os
import sqlite3
from app.db.database import init_db

with sqlite3.connect(os.path.join(os.environ["DATABASE_DIR"], "timelapse.db")) as conn:
    conn.execute("DELETE FROM alembic_version")
asyncio.run(init_db())
"""


def _run_in(data_dir: Path, script: str) -> subprocess.CompletedProcess[str]:
    # A process of its own: the app's engine is bound to DATABASE_DIR when the module is
    # imported, and the test harness refuses any SQLite file but the suite's own.
    return subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "DATABASE_DIR": str(data_dir)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_failed_migration_stops_init_db(tmp_path: Path) -> None:
    first = _run_in(tmp_path, _INIT_DB)
    assert first.returncode == 0, first.stderr[-2000:]

    second = _run_in(tmp_path, _FORGET_VERSION_THEN_INIT_DB)
    assert second.returncode != 0
    assert "already exists" in second.stderr
