# luxarch:test-db-bound asserter v7 — DO NOT edit the marker line above (repo.makefile_test_db_harness finds it).
#
# THE SUITE'S DATABASE, ASKED OF THE RUNNING SUITE
# Every test runs on the database TEST_DATABASE_URL names, in one transaction that is rolled back,
# whichever way the app opens its session; every Postgres connection is asked by its server where it
# is; and the run fails on any connection anywhere else, in the suite or in a process it started.
# The plugin package `luxarch_test_db/` beside this file does it; this block hands it the app's
# settings. How the run is judged, and what it does not claim: `luxarch --playbook test-isolation`.
#
# HOW TO WIRE IT (see: luxarch --doc FLEET-MAKEFILE-STANDARD, the test block)
#   1) Save this block ALONE as its own conftest.py in the suite's TEST_WORKDIR, one directory above
#      the tests (the repo root, or apps/<app>/ in a monorepo), so it covers every test path; keep
#      the repo's own conftest.py and its fixtures where they are.
#   2) Beside it, the package `luxarch_test_db/`: `luxarch --emit test-db-bound --update
#      conftest.py` writes it (and re-emits this block, keeping its settings).
#   3) Set the settings below. Nothing else is configurable on purpose.
# `make test` loads the plugin (`-p luxarch_test_db.identity -p luxarch_test_db.bound`, the package's
# `children` directory first on PYTHONPATH). A pytest run without it fails at this conftest.
#
# NOTE FOR ADOPTERS: this block is a conftest.py of its own, so its one import sits at its top; it
# defines no pytest hook or fixture: the plugin's own hooks and fixtures (`real_commits`,
# `luxarch_worker_database`) run beside the repo's.

import sys

# --- EDIT THIS: the app object (owns dependency_overrides) and the session dependency its routes use.
TEST_DB_APP_IMPORT = "app.web.main:app"
TEST_DB_SESSION_DEPENDENCY = "app.db.database:get_db"
# The app's own sessionmaker / async_sessionmaker: every session the app opens comes from it, and every
# test binds it to that test's rolled-back transaction.
TEST_DB_SESSION_FACTORY = "app.db.database:async_session"
# -----------------------------------------------------------------------------------------------------


def _luxarch_test_db_settings() -> None:
    """Hand the settings to the plugin pytest loaded with `-p luxarch_test_db.bound`."""
    plugin = sys.modules.get("luxarch_test_db.bound")
    if plugin is None:
        raise RuntimeError(
            "luxarch test-db-bound: the plugin luxarch_test_db.bound is not loaded, so nothing "
            "checks where this suite connects. `make test` loads it: `-p luxarch_test_db.identity "
            "-p luxarch_test_db.bound`, from the package luxarch_test_db/ in the suite's "
            "TEST_WORKDIR, where this conftest.py belongs too (`luxarch --emit test-db-bound "
            "--update conftest.py` writes the package beside it)"
        )
    plugin.configure(
        TEST_DB_APP_IMPORT, TEST_DB_SESSION_DEPENDENCY, TEST_DB_SESSION_FACTORY
    )


_luxarch_test_db_settings()
