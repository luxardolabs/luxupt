"""SQLAlchemy base classes and mixins."""

from datetime import datetime
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

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


_ModelT = TypeVar("_ModelT", bound="Base")


def with_column_defaults(obj: _ModelT) -> _ModelT:
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
