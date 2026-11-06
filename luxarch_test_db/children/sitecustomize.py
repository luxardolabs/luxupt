# luxarch:test-db-children asset v2 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit test-db-children`.
"""Every Python process the test run starts asks each Postgres connection where it is.

`make test` puts this directory first on PYTHONPATH, so Python runs this module as it starts any
process of the run: the suite itself, a child started by spawn or forkserver (Python 3.14's default
on Linux, so a plain `ProcessPoolExecutor()`), a subprocess CLI such as `alembic upgrade head`, an
xdist worker. It installs `luxarch_test_db.identity` there (a child started by fork inherits it
already), in the scope the run gave it (LUXARCH_TEST_DB_SCOPE), then runs the `sitecustomize` the
interpreter would have run without this one, if there is one.

A process with no SQLAlchemy has no engine to ask. One that has SQLAlchemy and cannot install the
check is stopped before it runs anything: Python reports a failing `sitecustomize` and goes on, which
would run the process unchecked.
"""

import importlib
import importlib.util
import os
import sys
from importlib.machinery import PathFinder


def _luxarch_test_db_children() -> None:
    here = os.path.dirname(os.path.realpath(__file__))
    if importlib.util.find_spec("sqlalchemy") is not None:
        # the package is found where this file is, never through PYTHONPATH: a repo directory on
        # the path would shadow installed packages in every process of the run
        top = os.path.dirname(os.path.dirname(here))
        sys.path.insert(0, top)
        try:
            # imported by name, once the package's directory is on the path: never at this module's
            # top, where it would import SQLAlchemy in a process that has none
            importlib.import_module("luxarch_test_db.identity").install()
        except BaseException as e:
            sys.stderr.write(
                "luxarch test-db-bound: could not install luxarch_test_db.identity in this "
                f"process ({type(e).__name__}: {e}); it is stopped before it runs, since it would "
                "otherwise reach any database unchecked\n"
            )
            sys.stderr.flush()
            os._exit(70)
        finally:
            if top in sys.path:
                sys.path.remove(top)
    # the image's own sitecustomize, which this one shadows on the path
    rest = [p for p in sys.path if os.path.realpath(p or os.curdir) != here]
    spec = PathFinder.find_spec("sitecustomize", rest)
    if spec is not None and spec.loader is not None:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)


_luxarch_test_db_children()
