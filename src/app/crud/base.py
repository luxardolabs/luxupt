"""Base CRUD class with generic operations."""

from typing import Any, Generic, TypeVar, cast

import config
from db.base import Base
from pydantic import BaseModel
from sqlalchemy import Column, func, select
from sqlalchemy.ext.asyncio import AsyncSession

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """Base class for CRUD operations."""

    def __init__(self, model: type[ModelType]) -> None:
        """Initialize with a SQLAlchemy model class."""
        self.model = model

    async def get(self, db: AsyncSession, id: Any) -> ModelType | None:
        """Get a single record by ID."""
        id_column = cast(Column[Any], self.model.id)  # type: ignore[attr-defined]
        result = await db.execute(select(self.model).where(id_column == id))
        return result.scalar_one_or_none()

    async def get_multi(
        self,
        db: AsyncSession,
        *,
        skip: int = 0,
        limit: int = config.DEFAULT_PAGE_SIZE,
    ) -> list[ModelType]:
        """Get multiple records with pagination."""
        result = await db.execute(select(self.model).offset(skip).limit(limit))
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, *, obj_in: CreateSchemaType) -> ModelType:
        """Create a new record."""
        db_obj = self.model(**obj_in.model_dump())
        db.add(db_obj)
        await db.flush()
        await db.refresh(db_obj)
        return db_obj

    async def create_from_dict(self, db: AsyncSession, *, data: dict) -> ModelType:
        """Create a new record from a dictionary."""
        db_obj = self.model(**data)
        db.add(db_obj)
        await db.flush()
        await db.refresh(db_obj)
        return db_obj

    async def update(
        self,
        db: AsyncSession,
        *,
        db_obj: ModelType,
        obj_in: UpdateSchemaType | dict,
    ) -> ModelType:
        """Update an existing record."""
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)

        db.add(db_obj)
        await db.flush()
        await db.refresh(db_obj)
        return db_obj

    async def delete(self, db: AsyncSession, *, id: Any) -> ModelType | None:
        """Delete a record by ID."""
        obj = await self.get(db, id)
        if obj:
            await db.delete(obj)
            await db.flush()
        return obj

    async def count(self, db: AsyncSession) -> int:
        """Count all records."""
        result = await db.execute(select(func.count()).select_from(self.model))
        return result.scalar() or 0

    async def exists(self, db: AsyncSession, id: Any) -> bool:
        """Check if a record exists by ID."""
        id_column = cast(Column[Any], self.model.id)  # type: ignore[attr-defined]
        result = await db.execute(select(id_column).where(id_column == id).limit(1))
        return result.scalar_one_or_none() is not None
