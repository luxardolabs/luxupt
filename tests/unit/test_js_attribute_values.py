"""A server value never reaches an attribute that runs as JavaScript.

The browser decodes an attribute's entities before Alpine or an `on*` handler runs it, so autoescaping
does not protect a value written there. Each macro carries its values in `data-*` attributes, where the
component reads them as text. Attributes are decoded here the way the browser decodes them.
"""

from html.parser import HTMLParser

import pytest

from app.web.templates import templates

HOSTILE = "x'); alert(1); //\"<b>"
_JS_PREFIXES = ("@", ":", "x-", "on")


class _Attributes(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attrs: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.attrs.extend((name, value or "") for name, value in attrs)


def _attributes(source: str, **context: str) -> list[tuple[str, str]]:
    parser = _Attributes()
    parser.feed(templates.env.from_string(source).render(hostile=HOSTILE, **context))
    return parser.attrs


MACROS = {
    "danger_confirm": (
        '{% from "macros/buttons.html" import danger_confirm %}'
        "{{ danger_confirm(hostile, confirm_text=hostile, hx_delete=hostile, hx_target=hostile, "
        "hx_swap=hostile, timeout=hostile) }}"
    ),
    "delete_icon_button": (
        '{% from "macros/icons.html" import delete_icon_button %}'
        "{{ delete_icon_button(hx_delete=hostile, hx_target=hostile, hx_swap=hostile, "
        "timeout=hostile, title=hostile) }}"
    ),
    "icon_toggle_button": (
        '{% from "macros/buttons.html" import icon_toggle_button %}'
        '{{ icon_toggle_button("bars-3", "x-mark", "Menu", hostile) }}'
    ),
    "radio_card": (
        '{% from "macros/forms.html" import radio_card %}'
        '{{ radio_card("mode", hostile, "Title", model="mode") }}'
    ),
    "collapsible_section": (
        '{% from "macros/navigation.html" import collapsible_section %}'
        '{% call collapsible_section("Title", hostile) %}body{% endcall %}'
    ),
    "chip_button": (
        '{% from "macros/buttons.html" import chip_button %}'
        '{{ chip_button(value=hostile, click="interval = Number($el.dataset.value)") }}'
    ),
}


@pytest.mark.parametrize("macro", MACROS)
def test_a_value_stays_out_of_every_javascript_attribute(macro: str) -> None:
    attrs = _attributes(MACROS[macro])

    in_javascript = [
        name
        for name, value in attrs
        if name.startswith(_JS_PREFIXES) and "alert(1)" in value
    ]
    assert in_javascript == []


@pytest.mark.parametrize("macro", MACROS)
def test_a_value_reaches_the_component_unchanged(macro: str) -> None:
    attrs = _attributes(MACROS[macro])

    assert any(name.startswith("data-") and value == HOSTILE for name, value in attrs)


def test_a_collapsible_section_adds_no_generic_name_to_the_alpine_scope() -> None:
    attrs = dict(_attributes(MACROS["collapsible_section"]))

    assert "storageKey:" in attrs["x-data"]
    assert " key:" not in attrs["x-data"]


def test_a_delete_icon_button_disarms_before_it_fires() -> None:
    attrs = dict(_attributes(MACROS["delete_icon_button"]))

    assert attrs["@click"].index("confirm = false") < attrs["@click"].index("htmx.ajax")


def test_a_danger_confirm_recovers_when_the_request_fails() -> None:
    attrs = dict(_attributes(MACROS["danger_confirm"]))

    assert ".finally(" in attrs["@click"]
    assert ".then(" not in attrs["@click"]


def test_a_stat_icon_glow_is_read_from_its_data_attribute() -> None:
    source = '{% from "macros/icons.html" import icon_box %}{{ icon_box("cog", color="info") }}'

    attrs = dict(_attributes(source))

    assert attrs["data-glow"] == "59, 130, 246"
    assert "59, 130, 246" not in attrs["onmouseenter"]
