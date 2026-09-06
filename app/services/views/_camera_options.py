"""Shape a camera list into the value/label pairs a `<select>` renders.

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
