# luxarch:test-db-plugin asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit test-db-plugin`.
"""luxarch's test-db-bound plugin: the suite's database, asked of the running suite.

The package sits beside the conftest.py that carries the test-db-bound block (`luxarch --emit
test-db-bound`), in the suite's TEST_WORKDIR, where `make test` loads it:

- `identity` (`luxarch --emit test-db-identity`): where a Postgres connection really is, asked of
  the server on that connection. Loaded with `-p luxarch_test_db.identity` in both of the test
  block's pytest runs, the suite's and the migration tests'.
- `bound` (`luxarch --emit test-db-bound-plugin`): every test on the throwaway database, in one
  rolled-back transaction, and the run judged at its end. Loaded with `-p luxarch_test_db.bound` in
  the suite's run; the conftest.py hands it the app's settings.
- `children/sitecustomize.py` (`luxarch --emit test-db-children`): installs `identity` in every
  Python process the run starts. The test block puts `children` first on PYTHONPATH.

`luxarch --emit test-db-bound --update conftest.py` writes and updates all of them beside the
conftest. How the run is judged, and what it does not claim: `luxarch --playbook test-isolation`.
"""
