"""The app's SQLite engine under the transaction recipe the test harness needs.

Two failures an adversarial pass reproduced on the app's engine, each fixed in app/db/database.py:

* a fresh database must still start and get incremental auto-vacuum: `VACUUM` cannot run inside a
  transaction, so init_db runs it on an autocommit connection the BEGIN listener leaves alone;
* a transaction that reads, then writes while another connection commits, must not fail with
  "database is locked": transactions begin IMMEDIATE, so the write lock is taken before the first
  read and the other writer waits for it (busy_timeout) instead.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.database import engine

_INIT_DB_ON_A_FRESH_DIRECTORY = """
import asyncio
from sqlalchemy import text
from app.db.database import engine, init_db

async def main() -> None:
    await init_db()
    async with engine.connect() as conn:
        print((await conn.execute(text("PRAGMA auto_vacuum"))).scalar())

asyncio.run(main())
"""


def test_init_db_starts_a_fresh_database_with_incremental_vacuum(
    tmp_path: Path,
) -> None:
    # A process of its own: the app's engine is bound to DATABASE_DIR when the module is imported.
    env = {**os.environ, "DATABASE_DIR": str(tmp_path)}
    run = subprocess.run(
        [sys.executable, "-c", _INIT_DB_ON_A_FRESH_DIRECTORY],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    assert run.stdout.strip().splitlines()[-1] == "2"  # INCREMENTAL


async def test_read_then_write_survives_a_writer_committing_in_between(
    real_commits: None,
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE busy_probe (id INTEGER PRIMARY KEY, v TEXT)")
        )
        await conn.execute(text("INSERT INTO busy_probe (v) VALUES ('seed')"))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def fetch_worker_commits_a_capture() -> None:
        async with maker() as worker:
            await worker.execute(text("INSERT INTO busy_probe (v) VALUES ('capture')"))
            await worker.commit()

    try:
        # A login: it reads (authenticate), the fetch worker commits meanwhile, then it writes
        # (update_last_login). Both must land; the worker waits for the lock if it has to.
        async with maker() as web:
            await web.execute(text("SELECT count(*) FROM busy_probe"))
            worker = asyncio.create_task(fetch_worker_commits_a_capture())
            await asyncio.sleep(0.2)
            await web.execute(text("UPDATE busy_probe SET v = 'login' WHERE id = 1"))
            await web.commit()
        await worker
        async with maker() as reader:
            rows = await reader.execute(text("SELECT v FROM busy_probe ORDER BY id"))
            assert list(rows.scalars().all()) == ["login", "capture"]
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE busy_probe"))
