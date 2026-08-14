"""SQLAlchemy base classes and mixins."""

from datetime import datetime
from typing import TYPE_CHECKING

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
