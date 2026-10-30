"""The scheduler panel offers exactly the encoder values its form schema accepts.

The panel's option lists carry labels, the schema's Literals carry the bounds; a value in one and
not the other is an option the save refuses, or a value the panel can never show selected.
"""

from app.schemas.settings_form_schema import PIXEL_FORMATS, PRESETS
from app.services.views._camera_options import PIXEL_FORMAT_OPTIONS, PRESET_OPTIONS


def test_preset_options_match_the_schema() -> None:
    assert tuple(o["value"] for o in PRESET_OPTIONS) == PRESETS


def test_pixel_format_options_match_the_schema() -> None:
    assert tuple(o["value"] for o in PIXEL_FORMAT_OPTIONS) == PIXEL_FORMATS
