from unittest.mock import patch

import pytest

try:
    from desktop_hud.app import HudWindow
except ValueError as exc:
    pytest.skip(f"desktop-hud GTK runtime unavailable: {exc}", allow_module_level=True)


def _window_for_interaction():
    window = object.__new__(HudWindow)
    window._interaction = {
        "namespace": "hotkeys",
        "correlation_id": "leader",
        "timeout_ms": 4000,
        "started_at": 1.0,
        "clear_namespace_on_release": True,
        "keyboard_mode": "exclusive",
    }
    window._interaction_timer = 123
    window.set_focusable = lambda _value: None
    window._refresh_keyboard_mode = lambda: None
    window._sync_overlay_visibility = lambda **_kwargs: None
    window.grab_focus = lambda: True
    window.has_focus = lambda: True
    window._refresh_input_region = lambda: None
    return window


def test_same_interaction_grab_renews_without_release():
    window = _window_for_interaction()
    released = []
    window.release_interaction = lambda reason: released.append(reason)

    with patch("desktop_hud.app.GLib.source_remove") as source_remove, patch(
        "desktop_hud.app.GLib.timeout_add",
        return_value=456,
    ) as timeout_add:
        result = window.grab_interaction(
            {
                "namespace": "hotkeys",
                "correlation_id": "leader",
                "timeout_ms": 5000,
                "clear_namespace_on_release": True,
                "keyboard_mode": "exclusive",
            }
        )

    assert result["ok"] is True
    assert result["renewed"] is True
    assert released == []
    assert window._interaction_timer == 456
    assert window._interaction["timeout_ms"] == 5000
    source_remove.assert_called_once_with(123)
    timeout_add.assert_called_once()


def test_different_interaction_grab_replaces_existing_capture():
    window = _window_for_interaction()
    released = []
    window.release_interaction = lambda reason: released.append(reason)

    with patch("desktop_hud.app.GLib.timeout_add", return_value=456):
        result = window.grab_interaction(
            {
                "namespace": "hotkeys",
                "correlation_id": "palette",
                "timeout_ms": 5000,
                "clear_namespace_on_release": True,
                "keyboard_mode": "exclusive",
            }
        )

    assert result["ok"] is True
    assert "renewed" not in result
    assert released == ["replaced"]
    assert window._interaction["correlation_id"] == "palette"


def test_key_activity_refreshes_interaction_timeout_without_regrab():
    window = _window_for_interaction()

    with patch("desktop_hud.app.time.time", return_value=42.0), patch(
        "desktop_hud.app.GLib.source_remove"
    ) as source_remove, patch("desktop_hud.app.GLib.timeout_add", return_value=456) as timeout_add:
        window._refresh_interaction_timer()

    assert window._interaction["started_at"] == 42.0
    assert window._interaction_timer == 456
    source_remove.assert_called_once_with(123)
    timeout_add.assert_called_once()
