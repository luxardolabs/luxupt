"""Shape domain lists into the option values a `<select>` renders.

Seven templates were each building this with the same
``{% set _ = camera_options.append({...}) %}`` loop — data shaping in the render layer
(fw.no_template_logic), duplicated verbatim. The view service owns shaping, so it lives here
once and the templates just iterate what they are handed.

Private to the views package (``_`` prefix per fw.service_naming: it is a helper, not a
service). Takes any object exposing ``camera_id``/``safe_name`` so the views package still
does not import an ORM model (fw.views_dont_import_models).
"""

from collections.abc import Sequence
from typing import Protocol


class _HasCameraIdentity(Protocol):
    """The two fields a camera option is built from."""

    @property
    def camera_id(self) -> str: ...

    @property
    def safe_name(self) -> str: ...


def build_camera_options(
    cameras: Sequence[_HasCameraIdentity],
) -> list[dict[str, str]]:
    """Return ``[{"value": camera_id, "label": "friendly name"}, ...]`` for a select."""
    return [
        {"value": camera.camera_id, "label": camera.safe_name.replace("_", " ")}
        for camera in cameras
    ]


def build_date_options(available_dates: "Sequence[object]") -> list[str]:
    """Return the dates as strings, which is what a `<select>` option value is.

    Five templates each wrote ``available_dates|map('string')|list`` — the same conversion,
    in the render layer (fw.no_template_logic). The list of real dates stays in the context
    for anything that needs it (a count, a comparison); this is the display-ready form.
    """
    return [str(day) for day in available_dates]


# The ffmpeg encoder settings the scheduler panel offers. Closed sets, so they belong in the
# view layer as data rather than as hardcoded <option> markup in the template
# (fw.no_template_logic / fw.no_hand_rolled_components_in_pages). Values are ffmpeg's own
# tokens; labels carry the operator-facing hint about the trade-off.
PIXEL_FORMAT_OPTIONS: list[dict[str, str]] = [
    {"value": "yuv420p", "label": "yuv420p"},
    {"value": "yuv444p", "label": "yuv444p"},
    {"value": "rgb24", "label": "rgb24"},
]

PRESET_OPTIONS: list[dict[str, str]] = [
    {"value": "ultrafast", "label": "Ultrafast (largest file)"},
    {"value": "superfast", "label": "Superfast"},
    {"value": "veryfast", "label": "Very Fast"},
    {"value": "faster", "label": "Faster"},
    {"value": "fast", "label": "Fast"},
    {"value": "medium", "label": "Medium (balanced)"},
    {"value": "slow", "label": "Slow"},
    {"value": "slower", "label": "Slower"},
    {"value": "veryslow", "label": "Very Slow (smallest file)"},
]
