"""Shared schema building blocks."""

from pydantic import BeforeValidator

# HTML filter selects submit value="" for the "All" placeholder; map that to None
# so optional enum query params validate real values and treat empty as unset.
EmptyStrToNone = BeforeValidator(lambda v: v if v != "" else None)
