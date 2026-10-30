"""Checking a settings payload against its update schema before it is saved.

The pages read settings back through validated read models, so a value stored past their bounds
turns every page that reads it into a 500. The browser's min/max is only a hint; this is the check
the views run on every settings save.
"""

from typing import Any

from pydantic import BaseModel, ValidationError


def settings_problem(schema: type[BaseModel], update_data: dict[str, Any]) -> str:
    """What is wrong with a settings payload, or "" when it fits `schema`.

    The pages read settings back through validated read models with the same bounds, so a value
    stored past them would turn every page that reads it into a 500. The browser's min/max is
    only a hint; this is the check.
    """
    fields = {k: v for k, v in update_data.items() if k in schema.model_fields}
    try:
        schema.model_validate(fields)
    except ValidationError as e:
        first = e.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        return f"Not saved: {field}: {first['msg']}"
    return ""
