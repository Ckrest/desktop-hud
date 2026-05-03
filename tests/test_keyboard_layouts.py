import pytest

from desktop_hud.keyboard_layouts import KeyboardLayoutError, load_keyboard_layout_asset, validate_keyboard_layout


def test_us_ansi_layout_loads_print_screen_key():
    layout = load_keyboard_layout_asset("us-ansi")

    key = next(item for item in layout["keys"] if item["code"] == "KEY_SYSRQ")

    assert key["label"] == "Print"
    assert "SysRq" in key["aliases"]


def test_keyboard_layout_validation_rejects_missing_code():
    with pytest.raises(KeyboardLayoutError):
        validate_keyboard_layout({"keys": [{"x": 0, "y": 0, "w": 1, "h": 1}]})
