# luxarch:naming-convention asset v1 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit naming-convention`.
# luxarch:naming-convention example v1 - emitted asset, re-emit to update; do not hand-edit.
"""Canonical fleet SQLAlchemy MetaData naming convention — emitted by `luxarch --emit naming-convention`.

Constraint and index names end up IN THE DATABASE, so every repo must use the SAME convention or
autogenerate churns and names can't be cleanly ALTER'd across repos. Required by
`repo.metadata_naming_convention`.

THE KEY DETAIL — use `column_0_N_name` (ALL columns), NOT `column_0_name` / `column_0_label` (the FIRST
column only). Two multi-column constraints that share a leading column would otherwise generate the SAME
name and collide the moment DDL runs (asyncpg DuplicateTableError) — silent until create_all/migrate.
The SQLAlchemy-docs example uses the first-column-only forms; do NOT copy it (BOUTIQUE-519: it collided
and failed 392 tests). `repo.metadata_naming_convention` flags the first-column-only forms for this
reason.

Note: Postgres truncates identifiers at 63 characters, so `column_0_N_name` on a wide constraint
truncates — that is expected, not a bug.

Usage:

    from sqlalchemy import MetaData
    from sqlalchemy.orm import DeclarativeBase

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    class Base(DeclarativeBase):
        metadata = metadata

Adopting on a MATURE schema: the next `alembic revision --autogenerate` will propose renaming existing
constraints to match the convention. Review that migration and apply it deliberately (name-only ALTERs) —
see `luxarch --playbook alembic`.
"""

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
