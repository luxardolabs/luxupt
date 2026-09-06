"""backfill empty timelapse camera_id from safe_name

A retired code path (``TimelapseService._create_timelapse_record``, now unreferenced) wrote
``camera_id=""`` when creating a timelapse row. Every browse filter and the
"does a timelapse already exist" check query ``Timelapse.camera_id``, so those rows are
invisible to the UI: they exist on disk and in the table but no camera filter can reach them.
Measured before writing this: 12 rows on one site, 1 on the other.

The rows do carry a valid ``camera_safe_name``, and ``cameras`` maps safe_name -> camera_id,
so the UUID is recoverable. This fills it in for exactly those rows.

Deliberately conservative:
  * only touches rows where camera_id is '' or NULL — a row with a real UUID is never rewritten
  * only fills where the safe_name resolves to exactly ONE camera, since safe_name is not
    unique; an ambiguous or unknown safe_name is left alone rather than guessed at
  * the downgrade is a no-op: restoring '' would be restoring the defect

Revision ID: 9f3d0d41053d
Revises: 006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f3d0d41053d"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Fill camera_id on orphaned timelapse rows, where safe_name resolves unambiguously."""
    op.execute(
        sa.text(
            """
            UPDATE timelapses
               SET camera_id = (
                     SELECT c.camera_id
                       FROM cameras c
                      WHERE c.safe_name = timelapses.camera_safe_name
                   )
             WHERE (camera_id = '' OR camera_id IS NULL)
               AND (
                     SELECT COUNT(*)
                       FROM cameras c
                      WHERE c.safe_name = timelapses.camera_safe_name
                   ) = 1
            """
        )
    )


def downgrade() -> None:
    """No-op: re-emptying camera_id would restore the bug this fixes."""
