"""DTOs for the signed-in user's own account pages."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PasswordChangeOutcome:
    """A redirect after a change, or the form again with its error and status."""

    redirect: str = ""
    status_code: int = 200
    context: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
