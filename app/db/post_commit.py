"""Run an irreversible side effect IFF this session's transaction durably commits.

Fleet standard: ADR-003 (``luxarch --doc adrs/003-irreversible-side-effects-after-commit``),
enforced by ``fw.side_effects_after_commit``.

An irreversible external side effect on a request path — deleting a file, an external
HTTP mutation — must not run inside the open request transaction. ``get_db`` commits once,
at request end, so an inline ``Path.unlink()`` runs *before* that commit; if anything after
it raises, ``get_db`` rolls the DB back but the file is already gone, leaving a row that
points at a missing file (corruption). Bind the effect to the transaction instead: queue it
here, and it fires only after the session's transaction durably commits.

Order for garbage, not corruption:
- delete: commit the row removal, then delete the blob (a leaked file is reclaimable garbage);
- create: write the blob, then commit the row (a stray file on rollback is garbage).

Do NOT use FastAPI ``BackgroundTasks`` / dependency-teardown ordering — in this stack those run
*before* the session teardown, making the effect guaranteed pre-commit rather than post-commit.
"""

from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def after_commit(session: AsyncSession | Session, fn: Callable[[], object]) -> None:
    """Queue ``fn`` to run once THIS session's current transaction durably commits.

    On rollback it never runs: the event fires only on commit, and both transaction
    owners (``get_db`` / a non-request context) close the session on rollback, so the
    one-shot listener is discarded with it. A ``rollback()`` that emitted no SQL fires
    no event anyway. Pass the session so the transaction binding is explicit, not ambient.
    """
    sync: Session = (
        session.sync_session if isinstance(session, AsyncSession) else session
    )
    event.listen(sync, "after_commit", lambda _s: fn(), once=True)
