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
from datetime import date
from typing import Protocol
from urllib.parse import urlencode


class PathFor(Protocol):
    """`request.app.url_path_for`: a route NAME and its path params -> the route's path."""

    def __call__(self, name: str, /, **path_params: object) -> str: ...


class CaptureIdentity(Protocol):
    """The fields a capture's lightbox URL is built from."""

    @property
    def id(self) -> int: ...

    @property
    def camera_safe_name(self) -> str: ...

    @property
    def timestamp(self) -> int: ...

    @property
    def interval(self) -> int: ...

    @property
    def capture_date(self) -> date: ...


class _HasTimelapseIdentity(Protocol):
    """The field a timelapse card's URLs are built from."""

    @property
    def id(self) -> int: ...


class _HasJobIdentity(Protocol):
    """The fields a job card's URLs are built from."""

    @property
    def job_id(self) -> str: ...

    @property
    def camera_safe_name(self) -> str: ...

    @property
    def interval(self) -> int: ...

    @property
    def target_date(self) -> date: ...

    @property
    def current_image(self) -> str | None: ...


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


def build_date_options(available_dates: Sequence[object]) -> list[str]:
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


def with_query(route_path: object, **params: object) -> str:
    """A route's path (resolved from its name) with an encoded query string."""
    return f"{route_path}?{urlencode(params)}"


def thumbnail_url(
    path: PathFor,
    *,
    camera: str,
    interval: int,
    capture_date: object,
    timestamp: object,
    size: int,
) -> str:
    """A captured frame's thumbnail, resolved from its route name (fw.urls_from_route_names).

    `capture_date` is a date or its ISO string, `timestamp` the frame's epoch seconds: the
    route's path segments, rendered as the template used to spell them.
    """
    return with_query(
        path(
            "images.get_image_thumbnail",
            camera=camera,
            interval=str(interval),
            capture_date=str(capture_date),
            timestamp=str(timestamp),
        ),
        size=size,
    )


def build_camera_card_urls(
    path: PathFor, camera_id: str, *, thumbnail: str = ""
) -> dict[str, str]:
    """The URLs a camera card links to, resolved from route names (fw.urls_from_route_names).

    Route knowledge belongs in one place: a path spelled into a template cannot be grepped
    when the route moves, and a card that silently links nowhere fails quietly. The browse
    links carry camera_id because that is the column the image and timelapse filters query.
    `thumbnail` is the latest capture's (see `thumbnail_url`), "" when there is none.
    """
    return {
        "settings": str(path("cameras.camera_settings_panel", camera_id=camera_id)),
        "images": with_query(path("images.images_page"), camera=camera_id),
        "timelapses": with_query(path("timelapses.timelapses_page"), camera=camera_id),
        "thumbnail": thumbnail,
    }


def job_card_urls(path: PathFor, job_id: str, *, thumbnail: str = "") -> dict[str, str]:
    """URLs for one job card, resolved from route names (fw.urls_from_route_names).

    `thumbnail` is the frame the job is on (see `job_thumbnail_url`), "" when there is none.
    """
    return {
        "poll": str(path("timelapse_jobs.job_progress_partial", job_id=job_id)),
        "cancel": str(path("timelapse_jobs.delete_job", job_id=job_id)),
        "target": f"#job-{job_id}",
        "thumbnail": thumbnail,
    }


def job_thumbnail_url(
    path: PathFor,
    *,
    camera_safe_name: str,
    interval: int,
    target_date: object,
    current_image: str | None,
) -> str:
    """The thumbnail of the frame a job is on, "" when it has none to show.

    The encoder records the frame's epoch timestamp (digits); that names a frame in the job's
    own day. A historical fetch records the frame's file path, whose day can differ from the
    job's, so it has no thumbnail URL and the card shows its placeholder.
    """
    if not current_image or not current_image.isdigit():
        return ""
    return thumbnail_url(
        path,
        camera=camera_safe_name,
        interval=interval,
        capture_date=target_date,
        timestamp=current_image,
        size=512,
    )


def build_job_cards[J: _HasJobIdentity](
    path: PathFor,
    jobs: Sequence[J],
) -> list[dict[str, J | dict[str, str]]]:
    """Each job paired with its card URLs, ready for the card macro.

    Paired here rather than handed over as a map keyed by job_id: indexing that map per item
    is a dynamic lookup in the render layer (fw.no_template_logic).
    """
    return [{"job": job, "urls": job_urls(path, job)} for job in jobs]


def job_urls(path: PathFor, job: _HasJobIdentity) -> dict[str, str]:
    """One job's card URLs, its current frame's thumbnail included."""
    return job_card_urls(
        path,
        job.job_id,
        thumbnail=job_thumbnail_url(
            path,
            camera_safe_name=job.camera_safe_name,
            interval=job.interval,
            target_date=job.target_date,
            current_image=job.current_image,
        ),
    )


def build_timelapse_cards[T: _HasTimelapseIdentity](
    path: PathFor,
    timelapses: Sequence[T],
) -> list[dict[str, T | dict[str, str]]]:
    """Each timelapse paired with its card URLs, resolved from route names."""
    return [
        {
            "timelapse": t,
            "urls": {
                "lightbox": str(
                    path("timelapses.timelapse_lightbox", timelapse_id=t.id)
                ),
                "video": str(
                    path("timelapses.serve_timelapse_video", timelapse_id=t.id)
                ),
                "delete": str(path("timelapses.delete_timelapse", timelapse_id=t.id)),
                "thumbnail": str(
                    path("timelapses.serve_timelapse_thumbnail", timelapse_id=t.id)
                ),
                "target": f"#timelapse-{t.id}",
            },
        }
        for t in timelapses
    ]
