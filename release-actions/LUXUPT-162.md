# LUXUPT-162: guard bump, login identity, error handling

Steps and heads-ups the release that ships this owes:

- **Login migration `d2f6a8c41e57` runs on start.** It stores every login stripped of surrounding whitespace and adds a unique index on the stripped login. It refuses to run, changing nothing, when two logins are the same once stripped (`bob` and ` bob`) or a login is only whitespace, and its error names the rows and the database file. The app cannot start until one of each pair is renamed. To rename, run a one-off container of the stack's own image with `python` as the entrypoint, open that database file with Python's `sqlite3` module, run `UPDATE users SET username = ? WHERE id = ?` with the new login and the row's id, commit, and start the stack again. Both sites held no such login on 2026-11-18.
- **Rolling back past this release needs `alembic downgrade a4d9e6b01c7f` first**, run with the new image: the previous image stops at start-up on the unknown revision. The downgrade drops the index and keeps the stripped logins.
- **Admin user form:** new and renamed logins are held to the same policy as `/setup` (letters, digits, `.`, `_`, `-`). Existing logins stay editable as they are.
- **Request ids:** every response but a 500 carries `X-Request-ID`, and a 500 page shows it as its reference. Request log lines name the route template, never the path or query, and no longer the submitted username on a failed sign-in.
