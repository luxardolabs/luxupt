"""The message a settings form's result partial shows when its schema refuses the form.

The settings forms are validated by their schemas in app/schemas/settings_form_schema.py before
anything is saved; this turns the first refusal into one line naming the field. Pydantic's message
never echoes the submitted value, so a refused credential is not repeated back.
"""

from pydantic import ValidationError


def validation_message(error: ValidationError) -> str:
    """'Not saved: <field>: <why>' for the first problem the schema found."""
    first = error.errors()[0]
    field = ".".join(str(part) for part in first["loc"]) or "form"
    return f"Not saved: {field}: {first['msg']}"
