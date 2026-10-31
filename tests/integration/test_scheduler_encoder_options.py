"""The scheduler panel offers exactly the encoder values its form schema accepts.

The panel's option lists carry labels, the schema's Literals carry the bounds; a value in one and
not the other is an option the save refuses, or a value the panel can never show selected. Read
from the rendered panel, so the check holds whatever builds the lists.
"""

from html.parser import HTMLParser

from httpx import AsyncClient

from app.schemas.settings_form_schema import PIXEL_FORMATS, PRESETS
from app.web.main import app


class _SelectOptions(HTMLParser):
    """Collect each `<select name=…>`'s option values, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.options: dict[str, list[str]] = {}
        self._current: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "select":
            self._current = attr.get("name") or ""
            self.options[self._current] = []
        elif tag == "option" and self._current is not None:
            self.options[self._current].append(attr.get("value") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._current = None


async def _panel_options(auth_client: AsyncClient) -> dict[str, list[str]]:
    resp = await auth_client.get(
        app.url_path_for("timelapse_scheduler.scheduler_panel")
    )
    assert resp.status_code == 200
    parser = _SelectOptions()
    parser.feed(resp.text)
    return parser.options


async def test_preset_options_match_the_schema(auth_client: AsyncClient) -> None:
    assert tuple((await _panel_options(auth_client))["preset"]) == PRESETS


async def test_pixel_format_options_match_the_schema(auth_client: AsyncClient) -> None:
    assert tuple((await _panel_options(auth_client))["pixel_format"]) == PIXEL_FORMATS
