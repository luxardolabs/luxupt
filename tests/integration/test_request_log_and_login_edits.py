"""What a request's log lines carry, and how a sign-in and the admin user form treat a stored row.

The request log names the path and an exception's type: a query value or a validation error's text
quotes what the client sent. A sign-in's fresh read of the user keeps a change still pending in the
session and holds the write lock until the sign-in ends. A stored login the edit form shows back
unchanged keeps its stored spelling and stays editable.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging_config import JsonFormatter
from app.crud.user_crud import user_crud
from app.services.views.timelapses_view_service import TimelapsesViewService
from app.web.main import app

SENT = "hunter2-the-value-the-client-sent"
PASSWORD = "a-long-enough-password"


class _Form(BaseModel):
    count: int


async def _raise_validation_error(self: TimelapsesViewService) -> None:
    _Form.model_validate({"count": SENT})


def _lines(records: list[logging.LogRecord]) -> list[str]:
    fmt = JsonFormatter(service="luxupt", version="test")
    return [fmt.format(r) for r in records]


@pytest_asyncio.fixture
async def failing_client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """A signed-in client that receives the app's 500 response instead of the re-raised error."""
    await user_crud.create_user(db, username="logged", password=PASSWORD)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        resp = await ac.post(
            "/login", data={"username": "logged", "password": PASSWORD}
        )
        assert resp.status_code in (302, 303)
        yield ac


@pytest_asyncio.fixture
async def admin_client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    await user_crud.create_user(
        db, username="owner-a", password=PASSWORD, is_admin=True
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        resp = await ac.post(
            "/login", data={"username": "owner-a", "password": PASSWORD}
        )
        assert resp.status_code in (302, 303)
        yield ac


def test_the_validation_error_used_here_quotes_the_value_sent() -> None:
    with pytest.raises(ValidationError) as caught:
        _Form.model_validate({"count": SENT})
    assert SENT in str(caught.value)


async def test_no_log_line_of_a_500_quotes_a_validation_errors_input(
    failing_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        TimelapsesViewService, "get_stats_context", _raise_validation_error
    )

    with caplog.at_level(logging.INFO):
        resp = await failing_client.get("/timelapses/jobs")

    assert resp.status_code == 500
    failed = [r for r in caplog.records if r.getMessage() == "HTTP request failed"]
    assert len(failed) == 1
    assert failed[0].__dict__["exception_type"] == "ValidationError"
    assert [line for line in _lines(caplog.records) if SENT in line] == []


async def test_no_log_line_of_a_422_quotes_the_query_value(
    auth_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        resp = await auth_client.get(f"/system/activity?page={SENT}")

    assert resp.status_code == 422
    assert SENT not in resp.text
    done = [r for r in caplog.records if r.getMessage() == "HTTP request completed"]
    assert done[-1].__dict__["route"] == "/system/activity"
    assert [line for line in _lines(caplog.records) if SENT in line] == []


async def test_a_routers_root_route_is_logged_by_its_template(
    auth_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        resp = await auth_client.get("/images?interval=60")

    assert resp.status_code == 200
    done = [r for r in caplog.records if r.getMessage() == "HTTP request completed"]
    assert done[-1].__dict__["route"] == "/images"


async def test_no_log_line_of_a_422_quotes_a_path_parameter_value(
    auth_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        resp = await auth_client.get(f"/system/partials/user-delete-confirm/{SENT}")

    assert resp.status_code == 422
    done = [r for r in caplog.records if r.getMessage() == "HTTP request completed"]
    assert (
        done[-1].__dict__["route"] == "/system/partials/user-delete-confirm/{user_id}"
    )
    assert [line for line in _lines(caplog.records) if SENT in line] == []


async def _raise_value_error_quoting_input(self: TimelapsesViewService) -> None:
    raise ValueError(f"invalid literal for int() with base 10: {SENT!r}")


async def test_the_request_log_of_a_500_carries_neither_the_path_nor_the_exception_text(
    failing_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        TimelapsesViewService, "get_stats_context", _raise_value_error_quoting_input
    )

    with caplog.at_level(logging.INFO):
        resp = await failing_client.get(f"/timelapses/jobs?x={SENT}")

    assert resp.status_code == 500
    [failed] = [r for r in caplog.records if r.getMessage() == "HTTP request failed"]
    [handled] = [r for r in caplog.records if r.getMessage() == "unhandled_exception"]
    assert failed.__dict__["request_id"] == handled.__dict__["request_id"]
    assert failed.__dict__["exception_type"] == "ValueError"
    assert failed.exc_info is None
    assert SENT not in _lines([failed])[0]


async def test_a_sign_in_keeps_a_change_still_pending_in_the_session(
    db: AsyncSession,
) -> None:
    user = await user_crud.create_user(db, username="pending", password=PASSWORD)
    user.must_change_password = True

    signed_in = await user_crud.authenticate(db, "pending", PASSWORD)

    assert signed_in is user
    assert user.must_change_password is True


async def test_a_sign_in_holds_the_write_lock_from_its_read_to_its_end(
    durable_db: async_sessionmaker[AsyncSession],
    write_lock_free: Callable[[], Awaitable[bool]],
) -> None:
    async with durable_db() as seed:
        await user_crud.create_user(seed, username="race-a", password=PASSWORD)
        await seed.commit()
    async with durable_db() as signing_in:
        assert await write_lock_free()
        user = await user_crud.authenticate(signing_in, "race-a", PASSWORD)
        assert user is not None
        assert not await write_lock_free()
        await signing_in.rollback()
    assert await write_lock_free()


@pytest.mark.parametrize("submitted", ["old name", " old name", "old name\t"])
async def test_a_stored_login_resubmitted_unchanged_stays_editable(
    db: AsyncSession, admin_client: AsyncClient, submitted: str
) -> None:
    other = await user_crud.create_user(
        db, username="old name", password=PASSWORD, is_admin=False
    )
    version = other.session_version

    resp = await admin_client.post(
        f"/system/users/{other.id}", data={"username": submitted, "is_admin": "true"}
    )

    await db.refresh(other)
    assert resp.status_code == 200
    assert "Username refused" not in resp.text
    assert other.is_admin is True
    assert other.username == "old name"
    assert other.session_version == version


async def test_renaming_to_another_users_login_is_refused(
    db: AsyncSession, admin_client: AsyncClient
) -> None:
    await user_crud.create_user(db, username="bob", password=PASSWORD)
    carol = await user_crud.create_user(db, username="carol", password=PASSWORD)

    resp = await admin_client.post(
        f"/system/users/{carol.id}", data={"username": " bob "}
    )

    await db.refresh(carol)
    assert "Username already exists" in resp.text
    assert carol.username == "carol"


async def test_creating_another_users_login_with_surrounding_whitespace_is_refused(
    db: AsyncSession, admin_client: AsyncClient
) -> None:
    await user_crud.create_user(db, username="bob", password=PASSWORD)

    resp = await admin_client.post(
        "/system/users",
        data={"username": "\tbob", "password": PASSWORD, "confirm_password": PASSWORD},
    )

    assert "Username already exists" in resp.text


async def test_a_failed_sign_in_never_logs_what_was_typed(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    typed = "Hunter2-typed-into-the-username-box" + "x" * 5000

    with caplog.at_level(logging.INFO):
        resp = await client.post(
            "/login", data={"username": typed, "password": "wrong"}
        )

    assert resp.status_code == 400
    [failed] = [r for r in caplog.records if r.getMessage() == "Failed login attempt"]
    assert "username" not in failed.__dict__
    assert [line for line in _lines(caplog.records) if "Hunter2-typed" in line] == []


async def test_a_successful_sign_in_logs_the_stored_login_not_what_was_typed(
    db: AsyncSession, client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    await user_crud.create_user(db, username="erin", password=PASSWORD)

    with caplog.at_level(logging.INFO):
        resp = await client.post(
            "/login", data={"username": "erin" + " " * 100_000, "password": PASSWORD}
        )

    assert resp.status_code in (302, 303)
    [signed] = [r for r in caplog.records if r.getMessage() == "Successful login"]
    assert signed.__dict__["username"] == "erin"
