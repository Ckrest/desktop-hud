"""Interactive editing controls for HUD elements."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk

from desktop_hud.snap import Rect, clamp_move_rect, snap_move_rect, snap_resize_rect

log = logging.getLogger(__name__)


@dataclass
class ManagedFrame:
    """UI widgets and metadata for one editable element frame."""

    frame: Gtk.Overlay
    content_widget: Gtk.Widget
    border: Gtk.Box
    badge: Gtk.Label
    handles: dict[str, Gtk.Box]
    editable: bool
    content_target_default: bool
    move_drag: Gtk.GestureDrag
    resize_drags: dict[str, Gtk.GestureDrag]


class EditController:
    """Owns edit mode interactions, selection state, and visual frames."""

    HANDLE_ALIGNMENT = {
        "nw": (Gtk.Align.START, Gtk.Align.START),
        "ne": (Gtk.Align.END, Gtk.Align.START),
        "sw": (Gtk.Align.START, Gtk.Align.END),
        "se": (Gtk.Align.END, Gtk.Align.END),
    }

    def __init__(
        self,
        window: Gtk.Window,
        overlay_cfg: dict,
        get_geometry: Callable[[str], Rect | None],
        get_other_rects: Callable[[str], list[Rect]],
        get_viewport_size: Callable[[], tuple[int, int]],
        apply_geometry: Callable[[str, int, int, int, int], None],
        on_commit: Callable[[], None],
        on_mode_changed: Callable[[bool], None],
    ):
        self.window = window
        self.get_geometry = get_geometry
        self.get_other_rects = get_other_rects
        self.get_viewport_size = get_viewport_size
        self.apply_geometry = apply_geometry
        self.on_commit = on_commit
        self.on_mode_changed = on_mode_changed

        self.show_borders = bool(overlay_cfg.get("show_borders_in_edit_mode", True))
        self.snap_threshold = int(overlay_cfg.get("snap_threshold_px", 12))

        min_size_cfg = overlay_cfg.get("min_size", {})
        self.min_width = max(8, int(min_size_cfg.get("width", 32)))
        self.min_height = max(8, int(min_size_cfg.get("height", 32)))

        interaction_cfg = overlay_cfg.get("interaction", {})
        self.snap_hysteresis_px = max(0, int(interaction_cfg.get("snap_hysteresis_px", 4)))
        self.debug_logging = bool(interaction_cfg.get("debug_logging", False))
        self.disable_snap_modifier_name = str(interaction_cfg.get("disable_snap_modifier", "Ctrl"))
        self.disable_snap_modifier_mask = self._parse_modifier(self.disable_snap_modifier_name)

        self.edit_mode = bool(overlay_cfg.get("edit_mode", False))
        self.hotkey_spec = str(overlay_cfg.get("edit_hotkey", "Ctrl+Alt+M"))
        self.hotkey = self._parse_hotkey(self.hotkey_spec)

        self.frames: dict[str, ManagedFrame] = {}
        self.selected_id: str | None = None
        self.interaction: dict | None = None
        self.shift_pressed = False
        self.snap_override_active = False

        self._telemetry = deque(maxlen=200)

        self._install_key_controller()

    def _install_key_controller(self) -> None:
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_key_pressed)
        key_controller.connect("key-released", self._on_key_released)
        self.window.add_controller(key_controller)
        self._key_controller = key_controller

    def register_element(self, elem_id: str, content_widget: Gtk.Widget, editable: bool) -> Gtk.Overlay:
        frame = Gtk.Overlay()
        frame.add_css_class("hud-element-frame")
        frame.set_child(content_widget)

        content_target_default = True
        try:
            content_target_default = bool(content_widget.get_can_target())
        except Exception:
            pass

        border = Gtk.Box()
        border.add_css_class("hud-element-border")
        border.set_hexpand(True)
        border.set_vexpand(True)
        border.set_can_target(False)
        frame.add_overlay(border)

        badge = Gtk.Label()
        badge.add_css_class("hud-geometry-badge")
        badge.set_halign(Gtk.Align.START)
        badge.set_valign(Gtk.Align.START)
        badge.set_margin_start(6)
        badge.set_margin_top(6)
        badge.set_can_target(False)
        frame.add_overlay(badge)

        resize_drags: dict[str, Gtk.GestureDrag] = {}
        handles: dict[str, Gtk.Box] = {}
        for name, (halign, valign) in self.HANDLE_ALIGNMENT.items():
            handle = Gtk.Box()
            handle.add_css_class("hud-resize-handle")
            handle.add_css_class(f"hud-resize-handle-{name}")
            handle.set_size_request(12, 12)
            handle.set_halign(halign)
            handle.set_valign(valign)

            drag = Gtk.GestureDrag.new()
            drag.set_button(Gdk.BUTTON_PRIMARY)
            try:
                drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            except Exception:
                pass
            drag.connect("drag-begin", self._on_resize_begin, elem_id, name)
            drag.connect("drag-update", self._on_resize_update, elem_id, name)
            drag.connect("drag-end", self._on_resize_end, elem_id, name)
            handle.add_controller(drag)

            frame.add_overlay(handle)
            handles[name] = handle
            resize_drags[name] = drag

        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_PRIMARY)
        try:
            click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        except Exception:
            pass
        click.connect("pressed", self._on_frame_pressed, elem_id)
        frame.add_controller(click)

        move_drag = Gtk.GestureDrag.new()
        move_drag.set_button(Gdk.BUTTON_PRIMARY)
        try:
            move_drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        except Exception:
            pass
        move_drag.connect("drag-begin", self._on_move_begin, elem_id)
        move_drag.connect("drag-update", self._on_move_update, elem_id)
        move_drag.connect("drag-end", self._on_move_end, elem_id)
        frame.add_controller(move_drag)

        self.frames[elem_id] = ManagedFrame(
            frame=frame,
            content_widget=content_widget,
            border=border,
            badge=badge,
            handles=handles,
            editable=editable,
            content_target_default=content_target_default,
            move_drag=move_drag,
            resize_drags=resize_drags,
        )
        self._refresh_visual(elem_id)
        return frame

    def unregister_element(self, elem_id: str) -> None:
        self.frames.pop(elem_id, None)
        if self.selected_id == elem_id:
            self.selected_id = None
        if self.interaction and self.interaction.get("id") == elem_id:
            self.interaction = None

    def is_edit_mode(self) -> bool:
        return self.edit_mode

    def set_edit_mode(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        if self.edit_mode == enabled:
            return False

        self.edit_mode = enabled
        if not enabled:
            self.interaction = None
            self.selected_id = None
            self.shift_pressed = False
            self.snap_override_active = False

        for elem_id in list(self.frames):
            self._refresh_visual(elem_id)

        self.on_mode_changed(self.edit_mode)
        return True

    def toggle_edit_mode(self) -> bool:
        return self.set_edit_mode(not self.edit_mode)

    def select(self, elem_id: str | None) -> None:
        self.selected_id = elem_id
        for candidate in list(self.frames):
            self._refresh_visual(candidate)

    def refresh_all(self) -> None:
        for elem_id in list(self.frames):
            self._refresh_visual(elem_id)

    def _refresh_visual(self, elem_id: str) -> None:
        managed = self.frames.get(elem_id)
        if managed is None:
            return

        is_selected = self.edit_mode and (elem_id == self.selected_id)

        if managed.editable:
            managed.frame.remove_css_class("hud-element-frame-readonly")
        else:
            managed.frame.add_css_class("hud-element-frame-readonly")

        if is_selected:
            managed.frame.add_css_class("hud-element-frame-selected")
        else:
            managed.frame.remove_css_class("hud-element-frame-selected")

        managed.border.set_visible(self.edit_mode and self.show_borders)
        managed.badge.set_visible(False)

        show_handles = self.edit_mode and managed.editable and is_selected
        for handle in managed.handles.values():
            handle.set_visible(show_handles)

        try:
            managed.frame.set_can_target(self.edit_mode)
        except Exception:
            pass

        try:
            if self.edit_mode:
                managed.content_widget.set_can_target(False)
            else:
                managed.content_widget.set_can_target(managed.content_target_default)
        except Exception:
            pass

        move_enabled = self.edit_mode and managed.editable
        self._set_controller_enabled(managed.move_drag, move_enabled)
        for drag in managed.resize_drags.values():
            self._set_controller_enabled(drag, move_enabled)

    @staticmethod
    def _set_controller_enabled(controller: Gtk.EventController, enabled: bool) -> None:
        try:
            controller.set_enabled(enabled)
        except Exception:
            pass

    def _set_resize_enabled(self, elem_id: str, enabled: bool) -> None:
        managed = self.frames.get(elem_id)
        if managed is None:
            return
        for drag in managed.resize_drags.values():
            self._set_controller_enabled(drag, enabled)

    def _show_geometry(self, elem_id: str, rect: Rect) -> None:
        managed = self.frames.get(elem_id)
        if managed is None:
            return
        managed.badge.set_text(f"x:{rect.x} y:{rect.y}  w:{rect.width} h:{rect.height}")
        managed.badge.set_visible(True)

    def _hide_geometry(self, elem_id: str) -> None:
        managed = self.frames.get(elem_id)
        if managed is None:
            return
        managed.badge.set_visible(False)

    @staticmethod
    def _rect_to_dict(rect: Rect) -> dict:
        return {
            "x": rect.x,
            "y": rect.y,
            "width": rect.width,
            "height": rect.height,
        }

    def _record_telemetry(self, kind: str, elem_id: str, payload: dict) -> None:
        entry = {
            "kind": kind,
            "element_id": elem_id,
            **payload,
        }
        self._telemetry.append(entry)
        if self.debug_logging:
            log.debug("interaction=%s", entry)

    def _is_snap_enabled(self) -> bool:
        return self.snap_threshold > 0 and not self.snap_override_active

    def _apply_hysteresis_fields(
        self,
        base: Rect,
        snapped: Rect,
        fields: tuple[str, ...],
    ) -> Rect:
        """
        Keep a snap lock only for fields that were actually snapped.

        This avoids fake "grid stepping" when free movement values are close
        to the previous frame's applied values.
        """
        release_threshold = self.snap_threshold + self.snap_hysteresis_px
        lock = self.interaction.setdefault("snap_lock", {}) if self.interaction else {}

        values = {
            "x": base.x,
            "y": base.y,
            "width": base.width,
            "height": base.height,
        }

        for field in fields:
            base_value = getattr(base, field)
            snapped_value = getattr(snapped, field)
            locked_value = lock.get(field)

            # Real snap candidate is active for this field.
            if snapped_value != base_value:
                values[field] = snapped_value
                lock[field] = snapped_value
                continue

            # No current snap; only keep prior lock while still inside release radius.
            if locked_value is not None:
                if abs(base_value - locked_value) <= release_threshold:
                    values[field] = locked_value
                    continue
                lock[field] = None

            values[field] = base_value

        return Rect(
            x=values["x"],
            y=values["y"],
            width=values["width"],
            height=values["height"],
        )

    def _on_frame_pressed(self, _gesture, _n_press, _x, _y, elem_id: str) -> None:
        if not self.edit_mode:
            return
        self.select(elem_id)

    def _on_move_begin(self, gesture, _start_x, _start_y, elem_id: str) -> None:
        if not self.edit_mode:
            return

        if self.interaction is not None:
            return

        managed = self.frames.get(elem_id)
        if managed is None or not managed.editable:
            return

        self.select(elem_id)

        start_rect = self.get_geometry(elem_id)
        if start_rect is None:
            return

        self._set_resize_enabled(elem_id, False)

        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.interaction = {
            "kind": "move",
            "id": elem_id,
            "start": start_rect,
            "changed": False,
            "snap_lock": {},
        }
        self._show_geometry(elem_id, start_rect)

    def _on_move_update(self, _gesture, offset_x, offset_y, elem_id: str) -> None:
        if not self.interaction:
            return
        if self.interaction.get("kind") != "move" or self.interaction.get("id") != elem_id:
            return

        start_rect: Rect = self.interaction["start"]
        proposed = Rect(
            x=start_rect.x + int(round(offset_x)),
            y=start_rect.y + int(round(offset_y)),
            width=start_rect.width,
            height=start_rect.height,
        )

        bounds_width, bounds_height = self.get_viewport_size()
        clamped = clamp_move_rect(proposed, bounds_width, bounds_height)
        if self._is_snap_enabled():
            snapped_raw = snap_move_rect(
                proposed=proposed,
                others=self.get_other_rects(elem_id),
                bounds_width=bounds_width,
                bounds_height=bounds_height,
                threshold=self.snap_threshold,
            )
            snapped = self._apply_hysteresis_fields(
                base=clamped,
                snapped=snapped_raw,
                fields=("x", "y"),
            )
        else:
            self.interaction["snap_lock"] = {}
            snapped = clamped

        self.apply_geometry(elem_id, snapped.x, snapped.y, snapped.width, snapped.height)
        self._show_geometry(elem_id, snapped)
        self.interaction["changed"] = True

        self._record_telemetry(
            "move",
            elem_id,
            {
                "offset": {
                    "x": float(offset_x),
                    "y": float(offset_y),
                },
                "proposed": self._rect_to_dict(proposed),
                "applied": self._rect_to_dict(snapped),
                "snap_enabled": self._is_snap_enabled(),
                "viewport": {
                    "width": bounds_width,
                    "height": bounds_height,
                },
            },
        )

    def _on_move_end(self, _gesture, _offset_x, _offset_y, elem_id: str) -> None:
        if not self.interaction:
            return
        if self.interaction.get("kind") != "move" or self.interaction.get("id") != elem_id:
            return

        self._set_resize_enabled(elem_id, True)

        changed = bool(self.interaction.get("changed"))
        self.interaction = None
        self._hide_geometry(elem_id)
        if changed:
            self.on_commit()

    def _on_resize_begin(self, gesture, _start_x, _start_y, elem_id: str, handle: str) -> None:
        if not self.edit_mode:
            return

        if self.interaction is not None:
            return

        managed = self.frames.get(elem_id)
        if managed is None or not managed.editable:
            return

        start_rect = self.get_geometry(elem_id)
        if start_rect is None:
            return

        self.select(elem_id)
        self._set_controller_enabled(managed.move_drag, False)

        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.interaction = {
            "kind": "resize",
            "id": elem_id,
            "handle": handle,
            "start": start_rect,
            "changed": False,
            "snap_lock": {},
        }
        self._show_geometry(elem_id, start_rect)

    def _on_resize_update(self, _gesture, offset_x, offset_y, elem_id: str, handle: str) -> None:
        if not self.interaction:
            return
        if self.interaction.get("kind") != "resize" or self.interaction.get("id") != elem_id:
            return
        if self.interaction.get("handle") != handle:
            return

        start_rect: Rect = self.interaction["start"]
        dx = int(round(offset_x))
        dy = int(round(offset_y))

        proposed = self._proposed_resize_rect(start_rect, handle, dx, dy)

        bounds_width, bounds_height = self.get_viewport_size()
        unsnapped = snap_resize_rect(
            proposed=proposed,
            start=start_rect,
            handle=handle,
            others=self.get_other_rects(elem_id),
            bounds_width=bounds_width,
            bounds_height=bounds_height,
            threshold=0,
            min_width=self.min_width,
            min_height=self.min_height,
            lock_aspect=self.shift_pressed,
        )

        if self._is_snap_enabled():
            snapped_raw = snap_resize_rect(
                proposed=proposed,
                start=start_rect,
                handle=handle,
                others=self.get_other_rects(elem_id),
                bounds_width=bounds_width,
                bounds_height=bounds_height,
                threshold=self.snap_threshold,
                min_width=self.min_width,
                min_height=self.min_height,
                lock_aspect=self.shift_pressed,
            )
            snapped = self._apply_hysteresis_fields(
                base=unsnapped,
                snapped=snapped_raw,
                fields=("x", "y", "width", "height"),
            )
        else:
            self.interaction["snap_lock"] = {}
            snapped = unsnapped

        self.apply_geometry(elem_id, snapped.x, snapped.y, snapped.width, snapped.height)
        self._show_geometry(elem_id, snapped)
        self.interaction["changed"] = True

        self._record_telemetry(
            "resize",
            elem_id,
            {
                "handle": handle,
                "offset": {
                    "x": float(offset_x),
                    "y": float(offset_y),
                },
                "proposed": self._rect_to_dict(proposed),
                "applied": self._rect_to_dict(snapped),
                "snap_enabled": self._is_snap_enabled(),
                "viewport": {
                    "width": bounds_width,
                    "height": bounds_height,
                },
            },
        )

    def _on_resize_end(self, _gesture, _offset_x, _offset_y, elem_id: str, handle: str) -> None:
        if not self.interaction:
            return
        if self.interaction.get("kind") != "resize" or self.interaction.get("id") != elem_id:
            return
        if self.interaction.get("handle") != handle:
            return

        managed = self.frames.get(elem_id)
        if managed is not None:
            self._set_controller_enabled(managed.move_drag, self.edit_mode and managed.editable)

        changed = bool(self.interaction.get("changed"))
        self.interaction = None
        self._hide_geometry(elem_id)
        if changed:
            self.on_commit()

    def _proposed_resize_rect(self, start_rect: Rect, handle: str, dx: int, dy: int) -> Rect:
        x = start_rect.x
        y = start_rect.y
        width = start_rect.width
        height = start_rect.height

        if "w" in handle:
            x = start_rect.x + dx
            width = start_rect.width - dx
        if "e" in handle:
            width = start_rect.width + dx
        if "n" in handle:
            y = start_rect.y + dy
            height = start_rect.height - dy
        if "s" in handle:
            height = start_rect.height + dy

        width = max(self.min_width, width)
        height = max(self.min_height, height)
        return Rect(x=x, y=y, width=width, height=height)

    def _parse_hotkey(self, hotkey_spec: str) -> tuple[int, Gdk.ModifierType] | None:
        spec = str(hotkey_spec or "").strip()
        if not spec:
            return None

        tokens = [token.strip() for token in spec.split("+") if token.strip()]
        if not tokens:
            return None

        key_token = tokens[-1]
        modifier_tokens = [token.upper() for token in tokens[:-1]]

        mod_mask = Gdk.ModifierType(0)
        for token in modifier_tokens:
            mod_mask |= self._parse_modifier(token)

        keyval = Gdk.keyval_from_name(key_token)
        if keyval == 0 and len(key_token) == 1:
            keyval = ord(key_token.lower())

        if keyval == 0:
            log.warning("Could not parse hotkey '%s'; hotkey toggle disabled", hotkey_spec)
            return None

        return (Gdk.keyval_to_upper(keyval), mod_mask)

    def _parse_modifier(self, token: str) -> Gdk.ModifierType:
        normalized = str(token or "").strip().upper()
        if normalized in {"", "NONE", "OFF", "DISABLED"}:
            return Gdk.ModifierType(0)

        alt_mask = getattr(Gdk.ModifierType, "ALT_MASK", Gdk.ModifierType(0))
        super_mask = getattr(Gdk.ModifierType, "SUPER_MASK", Gdk.ModifierType(0))
        meta_mask = getattr(Gdk.ModifierType, "META_MASK", Gdk.ModifierType(0))

        if normalized in {"CTRL", "CONTROL"}:
            return Gdk.ModifierType.CONTROL_MASK
        if normalized in {"ALT", "OPTION", "MOD1"}:
            return alt_mask
        if normalized == "SHIFT":
            return Gdk.ModifierType.SHIFT_MASK
        if normalized in {"SUPER", "WIN", "META"}:
            return super_mask | meta_mask

        log.warning("Unknown modifier '%s'", token)
        return Gdk.ModifierType(0)

    def _modifier_for_keyval(self, keyval: int) -> Gdk.ModifierType:
        control_keys = {
            getattr(Gdk, "KEY_Control_L", -1),
            getattr(Gdk, "KEY_Control_R", -1),
        }
        shift_keys = {
            getattr(Gdk, "KEY_Shift_L", -1),
            getattr(Gdk, "KEY_Shift_R", -1),
        }
        alt_keys = {
            getattr(Gdk, "KEY_Alt_L", -1),
            getattr(Gdk, "KEY_Alt_R", -1),
            getattr(Gdk, "KEY_Meta_L", -1),
            getattr(Gdk, "KEY_Meta_R", -1),
        }
        super_keys = {
            getattr(Gdk, "KEY_Super_L", -1),
            getattr(Gdk, "KEY_Super_R", -1),
        }

        if keyval in control_keys:
            return Gdk.ModifierType.CONTROL_MASK
        if keyval in shift_keys:
            return Gdk.ModifierType.SHIFT_MASK
        if keyval in alt_keys:
            return getattr(Gdk.ModifierType, "ALT_MASK", Gdk.ModifierType(0))
        if keyval in super_keys:
            return (
                getattr(Gdk.ModifierType, "SUPER_MASK", Gdk.ModifierType(0))
                | getattr(Gdk.ModifierType, "META_MASK", Gdk.ModifierType(0))
            )

        return Gdk.ModifierType(0)

    def _update_snap_override_from_key(self, keyval: int, pressed: bool) -> None:
        if self.disable_snap_modifier_mask == Gdk.ModifierType(0):
            return

        key_modifier = self._modifier_for_keyval(keyval)
        if key_modifier == Gdk.ModifierType(0):
            return

        if key_modifier & self.disable_snap_modifier_mask:
            self.snap_override_active = pressed

    def _on_key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        if keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
            self.shift_pressed = True

        self._update_snap_override_from_key(keyval, pressed=True)

        # Escape is a dedicated safety exit for edit mode regardless of hotkey config.
        if keyval == Gdk.KEY_Escape:
            if self.edit_mode:
                self.set_edit_mode(False)
                return True
            return False

        if not self.hotkey:
            return False

        expected_key, expected_mods = self.hotkey
        normalized_key = Gdk.keyval_to_upper(keyval)

        compare_mask = (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.SHIFT_MASK
            | getattr(Gdk.ModifierType, "ALT_MASK", Gdk.ModifierType(0))
            | getattr(Gdk.ModifierType, "SUPER_MASK", Gdk.ModifierType(0))
            | getattr(Gdk.ModifierType, "META_MASK", Gdk.ModifierType(0))
        )

        normalized_state = state & compare_mask
        if normalized_key == expected_key and (normalized_state & expected_mods) == expected_mods:
            self.toggle_edit_mode()
            return True

        return False

    def _on_key_released(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
            self.shift_pressed = False

        self._update_snap_override_from_key(keyval, pressed=False)
        return False

    def get_diagnostics(self) -> dict:
        interaction = None
        if self.interaction is not None:
            interaction = {
                "kind": self.interaction.get("kind"),
                "id": self.interaction.get("id"),
                "handle": self.interaction.get("handle"),
                "changed": bool(self.interaction.get("changed")),
            }

        return {
            "edit_mode": self.edit_mode,
            "selected_id": self.selected_id,
            "active_interaction": interaction,
            "snap": {
                "enabled": self._is_snap_enabled(),
                "threshold_px": self.snap_threshold,
                "hysteresis_px": self.snap_hysteresis_px,
                "override_active": self.snap_override_active,
                "disable_modifier": self.disable_snap_modifier_name,
            },
            "debug_logging": self.debug_logging,
            "recent_events": list(self._telemetry),
        }
