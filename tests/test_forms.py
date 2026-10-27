"""Card forms are parsed without starting the service or opening a database."""

import pytest

from app.preset import Preset


def test_preset_form_normalizes_name_and_preserves_id_and_timer():
    from app.forms import preset_from_form

    preset = preset_from_form(
        {
            "name": "  summer   night  ",
            "at": " 22:30 +0800 ",
            "power": "0",
            "mode": "4",
            "temp": "26",
            "fan": "2",
        },
        preset_id=7,
    )
    assert preset == Preset(
        id=7,
        name="summer night",
        power=0,
        mode=4,
        temp=26,
        fan=2,
        timer_enabled=True,
        timer_at="22:30 +0800",
    )


@pytest.mark.parametrize("raw", [None, "", "invalid", [], {}])
def test_malformed_numeric_fields_keep_existing_defaults(raw):
    from app.forms import preset_from_form

    assert preset_from_form(dict.fromkeys(("power", "mode", "temp", "fan"), raw)) == (
        Preset(name="", power=1, mode=1, temp=24, fan=0)
    )


def test_form_parser_does_not_add_new_clamping_or_timer_interpretation():
    from app.forms import preset_from_form

    preset = preset_from_form({"temp": "99", "at": "invalid"})
    assert (preset.temp, preset.timer_at, preset.timer_enabled) == (99, "invalid", True)


def test_settings_form_only_yields_supplied_known_truthy_values():
    from app.forms import settings_updates

    form = {
        "push_time": " 08:00 ",
        "night_start": "",
        "night_end": None,
        "night_target": "25",
        "owner_open_id": "untrusted",
    }
    assert settings_updates(form) == {"push_time": "08:00", "night_target": "25"}
    assert form["push_time"] == " 08:00 "


def test_settings_form_preserves_legacy_whitespace_and_zero_behavior():
    from app.forms import settings_updates

    assert settings_updates({"push_time": "  ", "night_target": 0}) == {"push_time": ""}
