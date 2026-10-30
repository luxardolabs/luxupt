"""The first admin, the forced password change, and recovery (FLEET-ADMIN-BOOTSTRAP-STANDARD).

The env-credential login (WEB_USERNAME/WEB_PASSWORD) is retired: the database is the only
credential store. These lock the behaviours this app wires around the emitted assets:

- startup seeds INITIAL_ADMIN_* only into an EMPTY users table, and never touches an existing one;
- /setup needs the token from the log and closes once anyone exists;
- a seeded, reset or admin-assigned password sends the user to /account/password at sign-in;
- the change form needs the current password and clears the flag;
- `python -m app.cli reset-password` recovers a locked-out account.
"""

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.core.admin_bootstrap_core_service as core_mod
from app.crud.user_crud import user_crud
from app.models.user_model import User
from app.services.core.admin_bootstrap_core_service import (
    AdminBootstrapCoreService,
    BootstrapOutcome,
    UnknownUserError,
)
from app.services.core.user_core_service import UserCoreService

PASSWORD = "a-long-enough-password"


@pytest.fixture(autouse=True)
def _token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "admin-setup-token"
    monkeypatch.setattr(core_mod, "SETUP_TOKEN_FILE", path)
    for key in ("INITIAL_ADMIN_USERNAME", "INITIAL_ADMIN_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(f"{key}_FILE", raising=False)
    return path


async def _user(db: AsyncSession, username: str) -> User | None:
    return await user_crud.get_by_username(db, username)


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        "/login", data={"username": username, "password": password}
    )
    assert resp.status_code in (302, 303), resp.text[:300]
    return resp.headers["location"]


# ── startup ─────────────────────────────────────────────────────────────────────────────────


async def test_seed_creates_the_first_admin_who_must_change_it(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "owner")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", PASSWORD)

    result = await AdminBootstrapCoreService().bootstrap(db)

    assert result.outcome == BootstrapOutcome.SEEDED
    user = await _user(db, "owner")
    assert user is not None
    assert user.is_admin
    assert user.must_change_password


async def test_seed_is_ignored_once_a_user_exists(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = await user_crud.create_user(db, username="owner", password=PASSWORD)
    before = existing.password_hash
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "owner")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "a-different-password-from-env")

    result = await AdminBootstrapCoreService().bootstrap(db)

    assert result.outcome == BootstrapOutcome.USERS_EXIST
    assert existing.password_hash == before  # the env password is not re-applied
    assert await user_crud.count(db) == 1


async def test_no_seed_opens_setup_with_a_logged_token(
    db: AsyncSession, _token_file: Path
) -> None:
    result = await AdminBootstrapCoreService().bootstrap(db)

    assert result.outcome == BootstrapOutcome.SETUP
    assert result.setup_token
    assert await asyncio.to_thread(_token_file.read_text) == result.setup_token


async def test_a_short_seed_password_opens_setup_instead_of_crashing(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INITIAL_ADMIN_USERNAME", "owner")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "short")

    result = await AdminBootstrapCoreService().bootstrap(db)

    assert result.outcome == BootstrapOutcome.SEED_REJECTED
    assert await user_crud.count(db) == 0


# ── /setup ──────────────────────────────────────────────────────────────────────────────────


def _setup_form(token: str) -> dict[str, str]:
    return {
        "token": token,
        "login": "owner",
        "password": PASSWORD,
        "password_confirm": PASSWORD,
    }


async def test_setup_refuses_a_wrong_token(
    client: AsyncClient, db: AsyncSession
) -> None:
    await AdminBootstrapCoreService().bootstrap(db)

    resp = await client.post("/setup", data=_setup_form("not-the-token"))

    assert resp.status_code == 403
    assert await user_crud.count(db) == 0


async def test_setup_with_the_logged_token_creates_the_admin_then_closes(
    client: AsyncClient, db: AsyncSession
) -> None:
    token = (await AdminBootstrapCoreService().bootstrap(db)).setup_token
    assert token

    resp = await client.post("/setup", data=_setup_form(token))
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")

    user = await _user(db, "owner")
    assert user is not None
    assert not user.must_change_password  # they chose it themselves
    assert (await client.get("/setup")).status_code == 404
    assert (await client.post("/setup", data=_setup_form(token))).status_code == 404


# ── sign-in, the forced change, recovery ────────────────────────────────────────────────────


async def test_a_seeded_admin_is_sent_to_the_change_form(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(
        db, username="owner", password=PASSWORD, must_change_password=True
    )
    assert await _login(client, "owner", PASSWORD) == "/account/password"


async def test_a_normal_sign_in_goes_to_cameras(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(db, username="owner", password=PASSWORD)
    assert await _login(client, "owner", PASSWORD) == "/cameras"


async def test_the_change_form_needs_the_current_password(
    client: AsyncClient, db: AsyncSession
) -> None:
    await user_crud.create_user(
        db, username="owner", password=PASSWORD, must_change_password=True
    )
    await _login(client, "owner", PASSWORD)
    new = "the-owners-own-password"

    page = await client.get("/account/password")
    assert page.status_code == 200
    assert "Choose your own" in page.text

    wrong = await client.post(
        "/account/password",
        data={
            "current_password": "not-it-at-all-123",
            "new_password": new,
            "confirm_password": new,
        },
    )
    assert wrong.status_code == 403

    ok = await client.post(
        "/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": new,
            "confirm_password": new,
        },
    )
    assert ok.status_code == 303
    user = await _user(db, "owner")
    assert user is not None
    assert not user.must_change_password
    assert await user_crud.authenticate(db, "owner", new) is not None


async def test_an_admin_assigned_password_must_be_changed(db: AsyncSession) -> None:
    users = UserCoreService(db)
    created = await users.create("helper", PASSWORD, is_admin=False)
    assert created.must_change_password

    row = await user_crud.get(db, created.id)
    assert row is not None
    await user_crud.update_user(db, row, must_change_password=False)
    updated = await users.update(created.id, password="an-admin-reset-password")
    assert updated is not None
    assert updated.must_change_password


async def test_a_retired_env_account_cannot_sign_in(
    client: AsyncClient, db: AsyncSession
) -> None:
    # What the migration leaves for a former WEB_PASSWORD account: its placeholder hash.
    db.add(User(username="legacy", password_hash="ENV_MANAGED", is_admin=True))
    await db.flush()
    resp = await client.post(
        "/login", data={"username": "legacy", "password": "ENV_MANAGED"}
    )
    assert resp.status_code == 400


async def test_reset_password_recovers_an_account(db: AsyncSession) -> None:
    # The recovery path `python -m app.cli reset-password <username>` runs (the CLI wrapper is
    # luxarch's emitted asset): a former env account gets a temporary password it must change.
    db.add(User(username="legacy", password_hash="ENV_MANAGED", is_admin=True))
    await db.flush()

    temporary = await AdminBootstrapCoreService().reset_password(db, "legacy")

    user = (
        await db.execute(select(User).where(User.username == "legacy"))
    ).scalar_one()
    assert user.must_change_password
    assert await user_crud.authenticate(db, "legacy", temporary) is not None


async def test_reset_password_refuses_an_unknown_user(db: AsyncSession) -> None:
    with pytest.raises(UnknownUserError):
        await AdminBootstrapCoreService().reset_password(db, "nobody")
