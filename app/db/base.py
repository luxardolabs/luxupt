"""SQLAlchemy base classes and mixins."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# The canonical fleet convention (luxarch --emit naming-convention). Constraint and index
# names end up IN THE DATABASE, so without a convention autogenerate emits non-deterministic
# names: every run churns drop/create, and an unnamed constraint cannot be cleanly ALTER'd
# later. Uses column_0_N_name (ALL columns) — NOT the SQLAlchemy-docs column_0_name form,
# which names by the first column only, so two multi-column constraints sharing a leading
# column generate the SAME name and collide the moment DDL runs.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    if TYPE_CHECKING:
        # Every concrete model declares `id: Mapped[int]` (primary key). Declaring it here — under
        # TYPE_CHECKING only, so SQLAlchemy never maps it at runtime — lets generic `ModelType: Base`
        # code (e.g. CRUDBase.get) reference `model.id` with real types instead of a `cast` hack.
        id: Mapped[int]


class TimestampMixin:
    """Mixin that adds created_at and updated_at timestamp columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def with_column_defaults[ModelT: Base](obj: ModelT) -> ModelT:
    """Apply the model's column defaults to a TRANSIENT (never-inserted) instance.

    SQLAlchemy resolves ``default=`` at INSERT time, so an object built in memory has
    ``None`` in every defaulted column until it is flushed. A read path that must return
    settings WITHOUT writing (fw.state_changing_get -- a GET must not mutate) therefore has
    to fill them itself, or the caller gets None where it expects a real value.
    """
    for column in obj.__table__.columns:
        if getattr(obj, column.name, None) is not None or column.default is None:
            continue
        arg = column.default.arg
        setattr(obj, column.name, arg(None) if callable(arg) else arg)
    return obj
