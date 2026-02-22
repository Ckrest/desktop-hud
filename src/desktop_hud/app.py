"""GTK4 Application with layer-shell overlay window."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from contextlib import contextmanager
from ctypes import CDLL
from dataclasses import dataclass
from pathlib import Path

# Must load libgtk4-layer-shell BEFORE any GI imports so it links before libwayland-client
CDLL("libgtk4-layer-shell.so")

os.environ.setdefault(
    "GI_TYPELIB_PATH",
    "/usr/local/lib/x86_64-linux-gnu/girepository-1.0",
)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gdk, GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from desktop_hud.config import PACKAGE_DIR, load_config
from desktop_hud.editor import EditController
from desktop_hud.elements import ELEMENT_TYPES
from desktop_hud.layouts import LayoutProfileError, LayoutProfileManager
from desktop_hud.snap import Rect

log = logging.getLogger(__name__)


@dataclass
class ElementRecord:
    """Runtime registry record for one HUD element."""

    element: object
    frame: Gtk.Widget
    source: str
    editable: bool


class HudWindow(Gtk.Window):
    """Transparent overlay window using GTK4 layer-shell."""

    def __init__(self, app: Gtk.Application, config: dict):
        super().__init__(application=app)
        self.config = config
        self.elements: dict[str, ElementRecord] = {}
        self._autosave_suppression = 0
        self._full_redraw_scheduled = False

        overlay_cfg = self.config.get("overlay", {})
        self.base_click_through = bool(overlay_cfg.get("click_through", True))
        self._load_interaction_config()

        self.profile_manager = LayoutProfileManager(PACKAGE_DIR, self.config)
        self.active_profile = self.profile_manager.default_profile

        self._setup_layer_shell()
        self._setup_container()
        self._setup_editor()
        self._load_elements(self.config.get("elements", []))
        self._initialize_profiles()

        if self.editor.is_edit_mode():
            self._on_editor_mode_changed(True)

    def _load_interaction_config(self):
        interaction_cfg = self.config.get("overlay", {}).get("interaction", {})
        self.force_full_redraw_on_move = bool(
            interaction_cfg.get("force_full_redraw_on_move", True),
        )

    def _setup_layer_shell(self):
        overlay_cfg = self.config.get("overlay", {})
        layer_name = overlay_cfg.get("layer", "overlay")
        namespace = overlay_cfg.get("namespace", "desktop-hud")

        LayerShell.init_for_window(self)

        layer = LayerShell.Layer.OVERLAY if layer_name == "overlay" else LayerShell.Layer.TOP
        LayerShell.set_layer(self, layer)

        # Anchor to all edges to fill the screen.
        for edge in (
            LayerShell.Edge.TOP,
            LayerShell.Edge.BOTTOM,
            LayerShell.Edge.LEFT,
            LayerShell.Edge.RIGHT,
        ):
            LayerShell.set_anchor(self, edge, True)

        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_namespace(self, namespace)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)

        self.connect("realize", self._on_realize)

    def _on_realize(self, *_args):
        self._refresh_input_region()
        self._refresh_keyboard_mode()

    def _refresh_keyboard_mode(self):
        keyboard_mode = (
            LayerShell.KeyboardMode.ON_DEMAND
            if self.editor.is_edit_mode()
            else LayerShell.KeyboardMode.NONE
        )
        LayerShell.set_keyboard_mode(self, keyboard_mode)

    def _refresh_input_region(self):
        effective_click_through = self.base_click_through and not self.editor.is_edit_mode()
        self._set_click_through(effective_click_through)

    def _set_click_through(self, enabled: bool):
        """Set input region for click-through or interactive mode."""
        try:
            surface = self.get_surface()
            if surface is None:
                return

            gi.require_version("GdkWayland", "4.0")
            from gi.repository import GdkWayland

            if not isinstance(surface, GdkWayland.WaylandSurface):
                return

            from cairo import RectangleInt, Region

            if enabled:
                surface.set_input_region(Region(RectangleInt(0, 0, 0, 0)))
                log.info("Click-through enabled")
                return

            width, height = self.get_viewport_size()
            surface.set_input_region(Region(RectangleInt(0, 0, max(1, width), max(1, height))))
            log.info("Interactive input enabled")
        except Exception:
            log.exception("Could not update click-through state")

    def _setup_container(self):
        """Create transparent fixed container for absolute positioning."""
        self.set_decorated(False)

        css_provider = Gtk.CssProvider()
        css_path = Path(__file__).with_name("style.css")
        css_provider.load_from_path(str(css_path))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.container = Gtk.Fixed()
        self.set_child(self.container)

    def _setup_editor(self):
        overlay_cfg = self.config.get("overlay", {})
        self.editor = EditController(
            window=self,
            overlay_cfg=overlay_cfg,
            get_geometry=self.get_element_rect,
            get_other_rects=self.get_other_element_rects,
            get_viewport_size=self.get_viewport_size,
            apply_geometry=self._apply_editor_geometry,
            on_commit=self._on_editor_commit,
            on_mode_changed=self._on_editor_mode_changed,
        )

    def _load_elements(self, element_configs: list[dict]):
        for elem_cfg in element_configs:
            self._add_element(elem_cfg)

    def _initialize_profiles(self):
        snapshot = self.get_elements_info()
        try:
            self.profile_manager.ensure_profile_exists(self.profile_manager.default_profile, snapshot)
            self.switch_profile(self.profile_manager.default_profile)
            self._maybe_autosave_last_used()
        except Exception:
            log.exception("Failed to initialize layout profiles")

    @contextmanager
    def _suspend_autosave(self):
        self._autosave_suppression += 1
        try:
            yield
        finally:
            self._autosave_suppression = max(0, self._autosave_suppression - 1)

    def _maybe_autosave_last_used(self):
        if self._autosave_suppression > 0:
            return
        try:
            self.profile_manager.save_last_used(self.get_elements_info())
        except Exception:
            log.exception("Autosave of last-used layout failed")

    def _add_element(self, elem_cfg: dict) -> bool:
        cfg = dict(elem_cfg)

        source = cfg.pop("__source", "config")
        elem_id = cfg.get("id")
        elem_type = cfg.get("type")

        if not elem_id or not elem_type:
            log.warning("Element missing id or type: %s", cfg)
            return False

        if elem_id in self.elements:
            log.warning("Duplicate element id: %s", elem_id)
            return False

        cls = ELEMENT_TYPES.get(elem_type)
        if cls is None:
            log.warning("Unknown element type '%s' for element '%s'", elem_type, elem_id)
            return False

        try:
            element = cls(cfg)
            content_widget = element.create_widget()

            pos = cfg.get("position", {})
            x = int(pos.get("x", 0))
            y = int(pos.get("y", 0))

            size = cfg.get("size", {})
            width = int(size.get("width", element.size[0]))
            height = int(size.get("height", element.size[1]))
            x, y, width, height = self._normalize_geometry(x, y, width, height)

            opacity = float(cfg.get("opacity", element.opacity))

            content_widget.set_size_request(width, height)
            content_widget.set_opacity(opacity)

            editable = source != "trait" or self.profile_manager.editable_trait_items
            frame = self.editor.register_element(elem_id, content_widget, editable=editable)
            frame.set_size_request(width, height)

            self.container.put(frame, x, y)

            element.position = (x, y)
            element.size = (width, height)
            element.opacity = opacity

            self.elements[elem_id] = ElementRecord(
                element=element,
                frame=frame,
                source=source,
                editable=editable,
            )
            log.info(
                "Added element '%s' (type=%s, source=%s, editable=%s) at (%d, %d)",
                elem_id,
                elem_type,
                source,
                editable,
                x,
                y,
            )
            return True
        except Exception:
            log.exception("Failed to create element '%s'", elem_id)
            return False

    def remove_element(self, elem_id: str, autosave: bool = True) -> bool:
        record = self.elements.pop(elem_id, None)
        if record is None:
            return False

        self.editor.unregister_element(elem_id)

        try:
            parent = record.frame.get_parent()
            if parent is not None and isinstance(parent, Gtk.Fixed):
                parent.remove(record.frame)
        except Exception:
            log.exception("Error removing frame for element '%s'", elem_id)

        try:
            record.element.destroy()
        except Exception:
            log.exception("Error destroying element '%s'", elem_id)

        if autosave:
            self._maybe_autosave_last_used()
        return True

    def update_element(
        self,
        elem_id: str,
        updates: dict,
        autosave: bool = True,
        from_interaction: bool = False,
    ) -> bool:
        record = self.elements.get(elem_id)
        if record is None:
            return False

        element = record.element
        content_widget = element.widget
        previous_rect = Rect(
            x=int(element.position[0]),
            y=int(element.position[1]),
            width=int(element.size[0]),
            height=int(element.size[1]),
        )

        if content_widget is None:
            return False

        if "opacity" in updates:
            opacity = float(updates["opacity"])
            content_widget.set_opacity(opacity)
            element.opacity = opacity

        if "position" in updates or "size" in updates:
            x, y = element.position
            width, height = element.size

            if "position" in updates:
                pos = updates["position"]
                x = int(pos.get("x", x))
                y = int(pos.get("y", y))

            if "size" in updates:
                size = updates["size"]
                width = int(size.get("width", width))
                height = int(size.get("height", height))

            x, y, width, height = self._normalize_geometry(x, y, width, height)
            self.container.move(record.frame, x, y)
            content_widget.set_size_request(width, height)
            record.frame.set_size_request(width, height)

            element.position = (x, y)
            element.size = (width, height)
            updated_rect = Rect(x=x, y=y, width=width, height=height)
            self._queue_geometry_redraw(
                record=record,
                previous_rect=previous_rect,
                updated_rect=updated_rect,
                from_interaction=from_interaction,
            )

        if autosave:
            self._maybe_autosave_last_used()
        return True

    def _queue_geometry_redraw(
        self,
        record: ElementRecord,
        previous_rect: Rect,
        updated_rect: Rect,
        from_interaction: bool,
    ) -> None:
        # Explicit redraws reduce stale artifacts on transparent overlays.
        self.container.queue_draw()
        record.frame.queue_draw()
        if record.element.widget is not None:
            record.element.widget.queue_draw()

        # If rect changed, force another container redraw for old position damage.
        if (
            previous_rect.x != updated_rect.x
            or previous_rect.y != updated_rect.y
            or previous_rect.width != updated_rect.width
            or previous_rect.height != updated_rect.height
        ):
            self.container.queue_draw()

        if from_interaction and self.force_full_redraw_on_move:
            self._schedule_full_redraw()

    def _schedule_full_redraw(self) -> None:
        if self._full_redraw_scheduled:
            return

        self._full_redraw_scheduled = True

        def run_full_redraw():
            self._full_redraw_scheduled = False
            self.queue_draw()
            self.container.queue_draw()
            return False

        GLib.timeout_add(16, run_full_redraw)

    def _normalize_geometry(self, x: int, y: int, width: int, height: int) -> tuple[int, int, int, int]:
        viewport_width, viewport_height = self.get_viewport_size()
        min_width = max(1, getattr(self.editor, "min_width", 32))
        min_height = max(1, getattr(self.editor, "min_height", 32))

        width = max(min_width, int(width))
        height = max(min_height, int(height))

        width = min(width, viewport_width)
        height = min(height, viewport_height)

        x = int(x)
        y = int(y)
        x = min(max(0, x), max(0, viewport_width - width))
        y = min(max(0, y), max(0, viewport_height - height))
        return x, y, width, height

    def _apply_editor_geometry(self, elem_id: str, x: int, y: int, width: int, height: int) -> None:
        self.update_element(
            elem_id,
            {
                "position": {"x": x, "y": y},
                "size": {"width": width, "height": height},
            },
            autosave=False,
            from_interaction=True,
        )

    def _on_editor_commit(self) -> None:
        self._maybe_autosave_last_used()

    def _on_editor_mode_changed(self, enabled: bool) -> None:
        self._refresh_keyboard_mode()
        self._refresh_input_region()
        if enabled:
            try:
                self.set_focusable(True)
                self.grab_focus()
            except Exception:
                log.debug("Could not focus window for edit hotkeys")

    def is_element_editable(self, elem_id: str) -> bool:
        record = self.elements.get(elem_id)
        return bool(record and record.editable)

    def has_element(self, elem_id: str) -> bool:
        return elem_id in self.elements

    def get_element_rect(self, elem_id: str) -> Rect | None:
        record = self.elements.get(elem_id)
        if record is None:
            return None

        x, y = record.element.position
        width, height = record.element.size
        return Rect(x=x, y=y, width=width, height=height)

    def get_other_element_rects(self, elem_id: str) -> list[Rect]:
        rects: list[Rect] = []
        for candidate_id, record in self.elements.items():
            if candidate_id == elem_id:
                continue
            x, y = record.element.position
            width, height = record.element.size
            rects.append(Rect(x=x, y=y, width=width, height=height))
        return rects

    def get_viewport_size(self) -> tuple[int, int]:
        width = self.container.get_allocated_width()
        height = self.container.get_allocated_height()
        if width > 0 and height > 0:
            return width, height

        surface = self.get_surface()
        if surface is not None:
            width = max(1, int(surface.get_width()))
            height = max(1, int(surface.get_height()))
            return width, height

        display = Gdk.Display.get_default()
        if display is not None:
            monitor = None
            if hasattr(display, "get_primary_monitor"):
                monitor = display.get_primary_monitor()

            if monitor is None and hasattr(display, "get_monitors"):
                monitors = display.get_monitors()
                if monitors is not None and monitors.get_n_items() > 0:
                    monitor = monitors.get_item(0)

            if monitor is not None:
                geometry = monitor.get_geometry()
                return int(geometry.width), int(geometry.height)

        return (1920, 1080)

    def set_edit_mode(self, enabled: bool) -> dict:
        changed = self.editor.set_edit_mode(enabled)
        return {
            "changed": changed,
            "edit_mode": self.editor.is_edit_mode(),
            "hotkey": self.editor.hotkey_spec,
        }

    def get_mode_info(self) -> dict:
        return {
            "edit_mode": self.editor.is_edit_mode(),
            "hotkey": self.editor.hotkey_spec,
            "force_full_redraw_on_move": self.force_full_redraw_on_move,
        }

    def get_mode_diagnostics(self) -> dict:
        diagnostics = self.editor.get_diagnostics()
        diagnostics["viewport"] = {
            "width": self.get_viewport_size()[0],
            "height": self.get_viewport_size()[1],
        }
        diagnostics["force_full_redraw_on_move"] = self.force_full_redraw_on_move
        return diagnostics

    def list_profiles(self) -> dict:
        names = self.profile_manager.list_profiles()
        return {
            "profiles": names,
            "active": self.active_profile,
            "default": self.profile_manager.default_profile,
            "last_used": self.profile_manager.last_used_profile,
        }

    def switch_profile(self, name: str) -> dict:
        try:
            geometry_map = self.profile_manager.load_profile(name)
        except FileNotFoundError:
            return {
                "ok": False,
                "error_code": "profile_not_found",
                "message": f"Profile '{name}' does not exist",
            }
        except LayoutProfileError as exc:
            return {
                "ok": False,
                "error_code": "invalid_profile_name",
                "message": str(exc),
            }
        except Exception:
            log.exception("Failed to load profile '%s'", name)
            return {
                "ok": False,
                "error_code": "profile_load_failed",
                "message": f"Could not load profile '{name}'",
            }

        with self._suspend_autosave():
            for elem_id, geometry in geometry_map.items():
                if not self.is_element_editable(elem_id):
                    continue
                if elem_id not in self.elements:
                    continue
                self.update_element(
                    elem_id,
                    {
                        "position": {"x": geometry["x"], "y": geometry["y"]},
                        "size": {
                            "width": geometry["width"],
                            "height": geometry["height"],
                        },
                    },
                    autosave=False,
                )

        self.active_profile = name
        self._maybe_autosave_last_used()
        return {
            "ok": True,
            "active": self.active_profile,
        }

    def save_profile(self, name: str) -> dict:
        try:
            path = self.profile_manager.save_profile(name, self.get_elements_info())
        except LayoutProfileError as exc:
            return {
                "ok": False,
                "error_code": "invalid_profile_name",
                "message": str(exc),
            }
        except Exception:
            log.exception("Failed to save profile '%s'", name)
            return {
                "ok": False,
                "error_code": "profile_save_failed",
                "message": f"Could not save profile '{name}'",
            }

        return {
            "ok": True,
            "name": name,
            "path": str(path),
        }

    def save_last_used_profile(self) -> dict:
        try:
            path = self.profile_manager.save_last_used(self.get_elements_info())
        except Exception:
            log.exception("Failed to save last-used profile")
            return {
                "ok": False,
                "error_code": "last_used_save_failed",
                "message": "Could not save last-used profile",
            }

        return {
            "ok": True,
            "name": self.profile_manager.last_used_profile,
            "path": str(path) if path is not None else None,
        }

    def get_current_layout_snapshot(self) -> dict:
        return {
            "active_profile": self.active_profile,
            "elements": self.get_elements_info(),
        }

    def reload_config(self, new_config: dict):
        """Diff elements and apply changes while preserving active profile where possible."""
        self.config = new_config
        self.base_click_through = bool(self.config.get("overlay", {}).get("click_through", True))
        self._load_interaction_config()

        self.profile_manager = LayoutProfileManager(PACKAGE_DIR, self.config)
        try:
            self.profile_manager.ensure_profile_exists(
                self.profile_manager.default_profile,
                self.get_elements_info(),
            )
        except Exception:
            log.exception("Failed to ensure default profile during reload")

        overlay_cfg = self.config.get("overlay", {})
        interaction_cfg = overlay_cfg.get("interaction", {})
        self.editor.show_borders = bool(overlay_cfg.get("show_borders_in_edit_mode", True))
        self.editor.snap_threshold = int(overlay_cfg.get("snap_threshold_px", 12))
        min_cfg = overlay_cfg.get("min_size", {})
        self.editor.min_width = max(8, int(min_cfg.get("width", 32)))
        self.editor.min_height = max(8, int(min_cfg.get("height", 32)))
        self.editor.snap_hysteresis_px = max(0, int(interaction_cfg.get("snap_hysteresis_px", 4)))
        self.editor.debug_logging = bool(interaction_cfg.get("debug_logging", False))
        self.editor.disable_snap_modifier_name = str(
            interaction_cfg.get("disable_snap_modifier", "Ctrl"),
        )
        self.editor.disable_snap_modifier_mask = self.editor._parse_modifier(
            self.editor.disable_snap_modifier_name,
        )
        self.editor.snap_override_active = False
        self.editor.hotkey_spec = str(overlay_cfg.get("edit_hotkey", "Ctrl+Alt+M"))
        self.editor.hotkey = self.editor._parse_hotkey(self.editor.hotkey_spec)

        new_elements = {
            e["id"]: e
            for e in new_config.get("elements", [])
            if isinstance(e, dict) and "id" in e
        }
        old_ids = set(self.elements.keys())
        new_ids = set(new_elements.keys())

        with self._suspend_autosave():
            # Remove elements no longer in config.
            for elem_id in old_ids - new_ids:
                self.remove_element(elem_id, autosave=False)

            # Add new elements.
            for elem_id in new_ids - old_ids:
                self._add_element(new_elements[elem_id])

            # Update existing element properties from config.
            for elem_id in old_ids & new_ids:
                cfg = new_elements[elem_id]
                source = cfg.get("__source", "config")
                record = self.elements.get(elem_id)
                if record is None:
                    continue
                editable = source != "trait" or self.profile_manager.editable_trait_items

                # If type/source changed, recreate the element.
                if (
                    cfg.get("type") != record.element.elem_type
                    or source != record.source
                    or editable != record.editable
                ):
                    self.remove_element(elem_id, autosave=False)
                    self._add_element(cfg)
                    continue

                updates = {}
                if "position" in cfg:
                    updates["position"] = cfg["position"]
                if "size" in cfg:
                    updates["size"] = cfg["size"]
                if "opacity" in cfg:
                    updates["opacity"] = cfg["opacity"]

                if updates:
                    self.update_element(elem_id, updates, autosave=False)

            # Re-apply active profile geometry on top of config positions.
            switch_result = self.switch_profile(self.active_profile)
            if not switch_result.get("ok"):
                self.switch_profile(self.profile_manager.default_profile)

        self.editor.set_edit_mode(bool(self.config.get("overlay", {}).get("edit_mode", False)))
        self.editor.refresh_all()

        self._refresh_input_region()
        self._maybe_autosave_last_used()

        log.info("Config reloaded: %d elements active", len(self.elements))

    def get_elements_info(self) -> list[dict]:
        result = []
        for elem_id, record in self.elements.items():
            element = record.element
            result.append({
                "id": elem_id,
                "type": element.elem_type,
                "position": {
                    "x": int(element.position[0]),
                    "y": int(element.position[1]),
                },
                "size": {
                    "width": int(element.size[0]),
                    "height": int(element.size[1]),
                },
                "opacity": float(element.opacity),
                "source": record.source,
                "editable": record.editable,
            })
        return result


class HudApplication(Gtk.Application):
    """Main application managing the overlay window and API server."""

    def __init__(self):
        super().__init__(application_id="com.systems.desktop-hud")
        self.window: HudWindow | None = None
        self.api_thread: threading.Thread | None = None

    def _with_trait_elements(self, config: dict) -> dict:
        merged = dict(config)
        merged_elements: list[dict] = []

        for elem in config.get("elements", []):
            if not isinstance(elem, dict):
                continue
            copy = dict(elem)
            copy.setdefault("__source", "config")
            merged_elements.append(copy)

        try:
            from desktop_hud.discovery import discover_trait_elements

            trait_elements = discover_trait_elements()
            for elem in trait_elements:
                if not isinstance(elem, dict):
                    continue
                copy = dict(elem)
                copy["__source"] = "trait"
                merged_elements.append(copy)

            if trait_elements:
                log.info("Loaded %d trait-discovered elements", len(trait_elements))
        except Exception:
            log.exception("Trait discovery failed (non-fatal)")

        merged["elements"] = merged_elements
        return merged

    def do_activate(self):
        config = self._with_trait_elements(load_config())
        self.window = HudWindow(self, config)
        self.window.present()

        api_cfg = config.get("api", {})
        if api_cfg.get("enabled", False):
            from desktop_hud.api import start_api_server

            port = api_cfg.get("port", 7820)
            self.api_thread = threading.Thread(
                target=start_api_server,
                args=(self.window, port),
                daemon=True,
            )
            self.api_thread.start()
            log.info("API server started on port %d", port)

        log.info("Desktop HUD started with %d elements", len(self.window.elements))

    def reload(self):
        """Reload config (called from SIGHUP handler)."""
        if self.window is None:
            return

        try:
            config = self._with_trait_elements(load_config())
            self.window.reload_config(config)
        except Exception:
            log.exception("Config reload failed")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    app = HudApplication()

    # SIGHUP triggers config reload.
    def on_sighup(*_args):
        GLib.idle_add(app.reload)

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, on_sighup)

    app.run(None)
