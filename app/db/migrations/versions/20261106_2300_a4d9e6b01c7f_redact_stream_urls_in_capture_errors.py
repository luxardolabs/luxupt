"""take stream URLs out of the capture errors already stored

A failed RTSP capture stored ffmpeg's error text whole, including the Protect stream URL. That URL
opens the camera with no login, and the text reaches every signed-in user through the activity log
and the cameras page. New failures are redacted when they happen. This cleans the rows already
stored: the URLs in them, and the host names in ffmpeg's "Failed to resolve hostname" lines.

Rows are read in batches and written only when the text changes, so a large table does not have to
fit in memory. A `details` value that is not valid JSON is cleared: it cannot be cleaned, and it
must not stop the app starting.

The downgrade changes nothing: the removed text cannot be restored and no release needs it.

Revision ID: a4d9e6b01c7f
Revises: 7c1e4f9a2b3d
"""

import json
import re
from collections.abc import Iterator, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "a4d9e6b01c7f"
down_revision: str | None = "7c1e4f9a2b3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 500
_URL = re.compile(r"(?<![a-zA-Z0-9+.-])([a-zA-Z0-9+.-]+)://\S+")
_RESOLVE = re.compile(r"(Failed to resolve hostname ).+?(: )")


def _redact(text: str) -> str:
    return _RESOLVE.sub(r"\1<redacted>\2", _URL.sub(r"\1://<redacted>", text))


def _clean_details(details: Any) -> Any:
    if details is None:
        return None
    try:
        stored = json.loads(details)
    except ValueError, TypeError, RecursionError:
        return None
    if isinstance(stored, dict) and isinstance(stored.get("error"), str):
        redacted = _redact(stored["error"])
        if redacted != stored["error"]:
            stored["error"] = redacted
            return json.dumps(stored)
    return details


def _rows(bind: sa.Connection, select: sa.TextClause) -> Iterator[tuple[Any, ...]]:
    last_id = 0
    while True:
        batch = bind.execute(select, {"last_id": last_id, "batch": _BATCH}).all()
        if not batch:
            return
        yield from (tuple(row) for row in batch)
        last_id = batch[-1][0]


def upgrade() -> None:
    bind = op.get_bind()
    captures = _rows(
        bind,
        sa.text(
            "SELECT id, error_message FROM captures "
            "WHERE id > :last_id AND error_message LIKE '%FFmpeg error%' "
            "ORDER BY id LIMIT :batch"
        ),
    )
    for capture_id, error_message in captures:
        redacted = _redact(error_message)
        if redacted != error_message:
            bind.execute(
                sa.text("UPDATE captures SET error_message = :text WHERE id = :id"),
                {"text": redacted, "id": capture_id},
            )

    activities = _rows(
        bind,
        sa.text(
            "SELECT id, message, details FROM activities "
            "WHERE id > :last_id AND message LIKE '%FFmpeg error%' "
            "ORDER BY id LIMIT :batch"
        ),
    )
    for activity_id, message, details in activities:
        redacted = _redact(message)
        cleaned = _clean_details(details)
        if redacted != message or cleaned != details:
            bind.execute(
                sa.text(
                    "UPDATE activities SET message = :text, details = :details WHERE id = :id"
                ),
                {"text": redacted, "details": cleaned, "id": activity_id},
            )


def downgrade() -> None:
    pass
