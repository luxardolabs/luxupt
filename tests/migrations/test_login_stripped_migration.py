"""`d2f6a8c41e57` stores every login stripped and allows one login per stripped spelling.

It refuses, changing nothing, when two stored logins are the same once stripped or a login is only
whitespace, and names the rows; the downgrade drops the index and keeps the stripped logins.
"""

import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.models.user_model import LOGIN_WHITESPACE

APP_DIR = Path(__file__).resolve().parents[2] / "app"
BEFORE = "a4d9e6b01c7f"
INDEX = "ix_users_login_identity"


def test_the_migration_strips_what_the_app_strips() -> None:
    path = next(
        (APP_DIR / "db" / "migrations" / "versions").glob("*_d2f6a8c41e57_*.py")
    )
    spec = importlib.util.spec_from_file_location("d2f6a8c41e57", path)
    assert spec is not None
    assert spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    assert revision.WHITESPACE == LOGIN_WHITESPACE


def _config(url: str) -> Config:
    cfg = Config(str(APP_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(APP_DIR / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _database(tmp_path: Path, logins: list[str]) -> tuple[Config, Path]:
    db_file = tmp_path / "timelapse.db"
    cfg = _config(f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(cfg, BEFORE)
    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.executemany(
            "INSERT INTO users (username, password_hash, is_admin, session_version) "
            "VALUES (?, 'x', 0, 7)",
            [(login,) for login in logins],
        )
    return cfg, db_file


def _logins(db_file: Path) -> list[str]:
    with closing(sqlite3.connect(db_file)) as reader:
        return [
            row[0] for row in reader.execute("SELECT username FROM users ORDER BY id")
        ]


def _has_index(db_file: Path) -> bool:
    with closing(sqlite3.connect(db_file)) as reader:
        names = [row[1] for row in reader.execute("PRAGMA index_list('users')")]
    return INDEX in names


def test_the_upgrade_stores_every_login_stripped_and_keeps_sessions(
    tmp_path: Path,
) -> None:
    cfg, db_file = _database(tmp_path, [" bob", "carol\t", "dave", "erin\u3000"])

    command.upgrade(cfg, "head")

    assert _logins(db_file) == ["bob", "carol", "dave", "erin"]
    assert _has_index(db_file)
    with closing(sqlite3.connect(db_file)) as reader:
        versions = {
            row[0] for row in reader.execute("SELECT session_version FROM users")
        }
    assert versions == {7}


def test_the_index_refuses_a_second_row_with_the_same_stripped_login(
    tmp_path: Path,
) -> None:
    cfg, db_file = _database(tmp_path, ["bob"])
    command.upgrade(cfg, "head")

    with (
        closing(sqlite3.connect(db_file)) as conn,
        pytest.raises(sqlite3.IntegrityError),
    ):
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, session_version) "
            "VALUES ('\u3000bob', 'x', 0, 1)"
        )


@pytest.mark.parametrize(
    ("logins", "named"),
    [([" bob", "bob"], "'bob'"), (["carol", "   "], "whitespace only")],
)
def test_the_upgrade_refuses_a_collision_and_changes_nothing(
    tmp_path: Path, logins: list[str], named: str
) -> None:
    cfg, db_file = _database(tmp_path, logins)

    with pytest.raises(RuntimeError, match=named) as refused:
        command.upgrade(cfg, "head")

    # It names the database this run opened (DATABASE_DIR can move it off the default path), and
    # no command to paste: a JSON log escapes the quotes such a command needs.
    message = str(refused.value)
    assert str(db_file) in message
    assert '"' not in message
    assert "docker" not in message

    assert _logins(db_file) == logins
    assert not _has_index(db_file)
    with closing(sqlite3.connect(db_file)) as reader:
        (version,) = reader.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    assert version == BEFORE


def test_the_downgrade_drops_the_index_and_keeps_the_stripped_logins(
    tmp_path: Path,
) -> None:
    cfg, db_file = _database(tmp_path, [" bob"])
    command.upgrade(cfg, "head")

    command.downgrade(cfg, BEFORE)

    assert _logins(db_file) == ["bob"]
    assert not _has_index(db_file)
