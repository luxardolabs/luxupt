"""Migration tests: each builds its own SQLite file at an older revision and upgrades it.

They run outside the test-db-bound block (`make test` runs this directory alone with
`--confcutdir`), because the block refuses the raw driver connections a migration test needs to
seed and read back rows below the ORM (`luxarch --playbook test-isolation`, "Migration tests").
Nothing above this directory is loaded, so the configuration env.py imports is set here, before
any test runs it: `app.core.config` refuses to import without ALLOWED_HOSTS.
"""

import os
import tempfile

os.environ.setdefault("ALLOWED_HOSTS", "test,localhost")
os.environ.setdefault(
    "DATABASE_DIR", tempfile.mkdtemp(prefix="luxupt-migrations-test-")
)
