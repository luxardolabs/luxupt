"""Pydantic schemas for User operations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    """Schema for creating a new User."""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)  # Plaintext password, will be hashed


class UserRead(BaseModel):
    """Schema for reading User data (excludes password)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str = Field(..., max_length=64)
    is_admin: bool
    auth_source: str = Field(..., max_length=32)  # "database" or "env"
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
