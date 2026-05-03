"""GTK4 Application with layer-shell overlay window."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
import json
from contextlib import contextmanager
from ctypes import CDLL
from dataclasses import dataclass, field
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
from desktop_hud.elements import ELEMENT_TYPES, ElementSkipRequested
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
    namespace: str | None = None
    local_id: str | None = None
    transient: bool = False


class HudWindow(Gtk.Window):
    """Transparent overlay window using GTK4 layer-shell."""

    def __init__(self, app: Gtk.Application, config: dict):
        super().__init__(application=app)
        self.config = config
        self.elements: dict[str, ElementRecord] = {}
        self._transient_timers: dict[str, int] = {}
        self._interaction: dict | None = None
        self._interaction_timer: int | None = None
        self._last_api_payloads: list[dict] = []
        self._last_callback_failures: list[dict] = []
        self._render_failures: list[dict] = []
        self._element_failures: list[dict] = []
        self._autosave_suppression = 0
        self._full_redraw_scheduled = False

        overlay_cfg = self.config.get("overlay", {})
        self.base_click_through = bool(overlay_cfg.get("click_through", True))
        self._load_interaction_config()

        self.profile_manager = LayoutProfileManager(PACKAGE_DIR, self.config)
        self.active_profile = self.profile_manager.default_profile
        self.active_profiles: list[str] = []

        self._setup_layer_shell()
        self._setup_container()
        self._setup_editor()
        self._setup_interaction_controller()
        self._load_startup_profiles()

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
            if self.editor.is_edit_mode() or self._interaction is not None
            else LayerShell.KeyboardMode.NONE
        )
        LayerShell.set_keyboard_mode(self, keyboard_mode)

    def _refresh_input_region(self):
        effective_click_through = (
            self.base_click_through
            and not self.editor.is_edit_mode()
            and self._interaction is None
        )
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

    def _setup_interaction_controller(self):
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect("key-pressed", self._on_interaction_key_pressed)
        key_controller.connect("key-released", self._on_interaction_key_released)
        self.add_controller(key_controller)
        self._interaction_key_controller = key_controller

    def _load_startup_profiles(self):
        """Load elements from startup profiles and last-used geometry."""
        profiles = self.profile_manager.startup_profiles

        # Backward compat: if config still has elements but no startup_profiles in config,
        # fall back to loading from config elements (log deprecation warning).
        config_elements = self.config.get("elements", [])
        has_explicit_startup = "startup_profiles" in self.config.get("layouts", {})

        if config_elements and not has_explicit_startup:
            log.warning(
                "Deprecated: loading elements from config 'elements' key. "
                "Move element definitions to layout files and use 'startup_profiles' instead.",
            )
            for elem_cfg in config_elements:
                self._add_element(elem_cfg)
        else:
            try:
                elements = self.profile_manager.load_profiles(profiles)
                for elem_cfg in elements:
                    self._add_element(elem_cfg)
                self.active_profiles = list(profiles)
                self.active_profile = profiles[-1] if profiles else self.profile_manager.default_profile
            except Exception:
                log.exception("Failed to load startup profiles %s", profiles)

        # Restore last-used geometry on top if available
        if self.profile_manager.autosave_last_used:
            try:
                last_used = self.profile_manager.load_profile(
                    self.profile_manager.last_used_profile,
                )
                last_used_by_id = {e.get("id"): e for e in last_used if e.get("id")}
                with self._suspend_autosave():
                    for elem_id, elem_cfg in last_used_by_id.items():
                        if elem_id in self.elements:
                            pos = elem_cfg.get("position", {})
                            size = elem_cfg.get("size", {})
                            self.update_element(elem_id, {
                                "position": {
                                    "x": pos.get("x", 0),
                                    "y": pos.get("y", 0),
                                },
                                "size": {
                                    "width": size.get("width", 100),
                                    "height": size.get("height", 100),
                                },
                            }, autosave=False)
            except FileNotFoundError:
                pass
            except Exception:
                log.exception("Failed to restore last-used layout")

        self._maybe_autosave_last_used()

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

    @staticmethod
    def _merge_element_config(base: dict, updates: dict) -> dict:
        """Recursively merge PATCH updates into an element config."""
        merged = dict(base)
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = HudWindow._merge_element_config(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _global_element_id(namespace: str | None, local_id: str) -> str:
        local = str(local_id).strip()
        if namespace:
            return f"{namespace}:{local}"
        return local

    @staticmethod
    def _payload_summary(payload: dict, endpoint: str) -> dict:
        elements = payload.get("elements")
        return {
            "endpoint": endpoint,
            "timestamp": time.time(),
            "namespace": payload.get("namespace"),
            "replace_namespace": payload.get("replace_namespace"),
            "ttl_ms": payload.get("ttl_ms"),
            "element_count": len(elements) if isinstance(elements, list) else None,
            "keys": sorted(str(key) for key in payload.keys()),
        }

    def record_api_payload(self, endpoint: str, payload: dict) -> None:
        self._last_api_payloads.append(self._payload_summary(payload, endpoint))
        self._last_api_payloads = self._last_api_payloads[-25:]

    def _record_element_failure(self, elem_id: str, message: str, payload: dict | None = None) -> None:
        self._element_failures.append(
            {
                "timestamp": time.time(),
                "id": elem_id,
                "message": message,
                "type": (payload or {}).get("type"),
                "namespace": (payload or {}).get("__namespace"),
            },
        )
        self._element_failures = self._element_failures[-50:]

    def _recreate_element_widget(self, elem_id: str, merged_config: dict) -> bool:
        """Rebuild an element widget in-place for backend-sensitive config updates."""
        record = self.elements.get(elem_id)
        if record is None:
            return False

        old_element = record.element
        elem_type = str(merged_config.get("type", getattr(old_element, "elem_type", "")))
        cls = ELEMENT_TYPES.get(elem_type)
        if cls is None:
            log.warning("Cannot recreate element '%s': unknown type '%s'", elem_id, elem_type)
            return False

        try:
            new_element = cls(merged_config)
            new_widget = new_element.create_widget()
        except ElementSkipRequested as exc:
            log.warning("Recreate skipped for element '%s': %s", elem_id, exc)
            return False
        except Exception:
            log.exception("Failed to recreate element '%s'", elem_id)
            return False

        if new_widget is None:
            log.warning("Recreate produced no widget for element '%s'", elem_id)
            return False

        pos = merged_config.get("position", {})
        x = int(pos.get("x", old_element.position[0]))
        y = int(pos.get("y", old_element.position[1]))

        size = merged_config.get("size", {})
        width = int(size.get("width", old_element.size[0]))
        height = int(size.get("height", old_element.size[1]))
        x, y, width, height = self._normalize_geometry(x, y, width, height)

        opacity = float(merged_config.get("opacity", old_element.opacity))
        new_widget.set_size_request(width, height)
        new_widget.set_opacity(opacity)
        record.frame.set_size_request(width, height)
        self.container.move(record.frame, x, y)
        record.frame.set_child(new_widget)

        managed = self.editor.frames.get(elem_id)
        if managed is not None:
            managed.content_widget = new_widget
            try:
                managed.content_target_default = bool(new_widget.get_can_target())
            except Exception:
                managed.content_target_default = True
            self.editor.refresh_all()

        merged_config["position"] = {"x": x, "y": y}
        merged_config["size"] = {"width": width, "height": height}
        merged_config["opacity"] = opacity
        new_element.position = (x, y)
        new_element.size = (width, height)
        new_element.opacity = opacity
        new_element.config = merged_config
        record.element = new_element

        try:
            old_element.destroy()
        except Exception:
            log.exception("Error destroying old widget for element '%s'", elem_id)

        self.container.queue_draw()
        record.frame.queue_draw()
        new_widget.queue_draw()
        log.info("Recreated element '%s' (type=%s) after runtime config update", elem_id, elem_type)
        return True

    def _add_element(self, elem_cfg: dict) -> bool:
        cfg = dict(elem_cfg)

        source = cfg.pop("__source", "config")
        namespace = cfg.pop("__namespace", None)
        local_id = cfg.pop("__local_id", None)
        transient = bool(cfg.pop("__transient", False))
        elem_id = cfg.get("id")
        elem_type = cfg.get("type")

        if not elem_id or not elem_type:
            log.warning("Element missing id or type: %s", cfg)
            self._record_element_failure(str(elem_id or ""), "missing id or type", cfg)
            return False

        if elem_id in self.elements:
            log.warning("Duplicate element id: %s", elem_id)
            self._record_element_failure(str(elem_id), "duplicate element id", cfg)
            return False

        cls = ELEMENT_TYPES.get(elem_type)
        if cls is None:
            log.warning("Unknown element type '%s' for element '%s'", elem_type, elem_id)
            self._record_element_failure(str(elem_id), f"unknown element type '{elem_type}'", cfg)
            return False

        try:
            element = cls(cfg)
            content_widget = element.create_widget()
            if content_widget is None:
                log.info("Element '%s' was skipped by source policy", elem_id)
                return False

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

            editable = True
            frame = self.editor.register_element(elem_id, content_widget, editable=editable)
            frame.set_size_request(width, height)

            log.info("Positioning element '%s' at (%d, %d) with size %dx%d",
                     elem_id, x, y, width, height)
            log.info("Viewport size: %dx%d", *self.get_viewport_size())

            self.container.put(frame, x, y)

            element.position = (x, y)
            element.size = (width, height)
            element.opacity = opacity

            self.elements[elem_id] = ElementRecord(
                element=element,
                frame=frame,
                source=source,
                editable=editable,
                namespace=namespace,
                local_id=local_id,
                transient=transient,
            )

            self._attach_runtime_click_callback(elem_id, frame)

            # Verify position was actually applied
            from gi.repository import GLib
            def verify_position():
                alloc = frame.get_allocation()
                log.info("Element '%s' actual allocation: x=%d, y=%d, width=%d, height=%d",
                         elem_id, alloc.x, alloc.y, alloc.width, alloc.height)
                return False
            GLib.idle_add(verify_position)

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
        except ElementSkipRequested as exc:
            log.info("Skipped element '%s': %s", elem_id, exc)
            self._record_element_failure(str(elem_id), f"skipped: {exc}", cfg)
            return False
        except Exception:
            log.exception("Failed to create element '%s'", elem_id)
            self._record_element_failure(str(elem_id), "creation exception", cfg)
            return False

    def _attach_runtime_click_callback(self, elem_id: str, frame: Gtk.Widget) -> None:
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._on_runtime_element_clicked, elem_id)
        frame.add_controller(click)

    def _on_runtime_element_clicked(self, _gesture, _n_press, x, y, elem_id: str) -> None:
        if self.editor.is_edit_mode():
            return
        record = self.elements.get(elem_id)
        if record is None:
            return
        self._emit_callback(
            "element.clicked",
            {
                "id": record.local_id or elem_id,
                "global_id": elem_id,
                "x": float(x),
                "y": float(y),
            },
            record=record,
        )

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

    def _prepare_namespaced_element(
        self,
        namespace: str,
        elem_cfg: dict,
        source: str = "api",
        transient: bool = False,
    ) -> dict:
        local_id = str(elem_cfg.get("id", "")).strip()
        if not local_id:
            raise ValueError("Namespaced element is missing id")
        cfg = dict(elem_cfg)
        cfg["id"] = self._global_element_id(namespace, local_id)
        cfg["__namespace"] = namespace
        cfg["__local_id"] = local_id
        cfg["__source"] = source
        cfg["__transient"] = transient
        return cfg

    def _namespace_element_ids(self, namespace: str) -> list[str]:
        return [
            elem_id
            for elem_id, record in self.elements.items()
            if record.namespace == namespace
        ]

    def list_namespaces(self) -> dict:
        namespaces: dict[str, dict] = {}
        for record in self.elements.values():
            if not record.namespace:
                continue
            entry = namespaces.setdefault(
                record.namespace,
                {"namespace": record.namespace, "element_count": 0, "transient_count": 0},
            )
            entry["element_count"] += 1
            if record.transient:
                entry["transient_count"] += 1
        return {"namespaces": sorted(namespaces.values(), key=lambda item: item["namespace"])}

    def replace_namespace_elements(
        self,
        namespace: str,
        elements: list[dict],
        replace: bool = True,
        source: str = "api",
        transient: bool = False,
        autosave: bool = True,
    ) -> dict:
        namespace = str(namespace).strip()
        if not namespace:
            return {"ok": False, "error_code": "namespace_required", "message": "namespace is required"}
        if not isinstance(elements, list):
            return {"ok": False, "error_code": "elements_required", "message": "elements must be a list"}

        added: list[str] = []
        failed: list[dict] = []
        with self._suspend_autosave():
            if replace:
                for elem_id in self._namespace_element_ids(namespace):
                    self.remove_element(elem_id, autosave=False)
            for raw in elements:
                if not isinstance(raw, dict):
                    failed.append({"id": None, "message": "element must be an object"})
                    continue
                try:
                    cfg = self._prepare_namespaced_element(namespace, raw, source=source, transient=transient)
                except ValueError as exc:
                    failed.append({"id": raw.get("id"), "message": str(exc)})
                    continue
                if self._add_element(cfg):
                    added.append(str(raw.get("id")))
                else:
                    failed.append({"id": raw.get("id"), "message": "failed to create element"})

        if autosave and not transient:
            self._maybe_autosave_last_used()
        return {
            "ok": not failed,
            "namespace": namespace,
            "added": added,
            "failed": failed,
            "element_count": len(self._namespace_element_ids(namespace)),
        }

    def update_namespaced_element(self, namespace: str, local_id: str, updates: dict) -> bool:
        return self.update_element(self._global_element_id(namespace, local_id), updates)

    def delete_namespace(self, namespace: str, autosave: bool = True) -> dict:
        namespace = str(namespace).strip()
        removed = []
        with self._suspend_autosave():
            for elem_id in self._namespace_element_ids(namespace):
                if self.remove_element(elem_id, autosave=False):
                    removed.append(elem_id)
        self._cancel_transient_timer(namespace)
        if autosave:
            self._maybe_autosave_last_used()
        return {"ok": True, "namespace": namespace, "removed": len(removed)}

    def create_transient(self, payload: dict) -> dict:
        namespace = str(payload.get("namespace", "transient")).strip()
        ttl_ms = max(1, int(payload.get("ttl_ms", 3000)))
        replace = bool(payload.get("replace_namespace", True))
        elements = payload.get("elements") or []
        result = self.replace_namespace_elements(
            namespace=namespace,
            elements=elements,
            replace=replace,
            source="transient",
            transient=True,
            autosave=False,
        )
        if not result.get("ok"):
            return result
        self._cancel_transient_timer(namespace)

        def expire_namespace():
            self.delete_namespace(namespace, autosave=False)
            self._transient_timers.pop(namespace, None)
            return False

        timer_id = GLib.timeout_add(ttl_ms, expire_namespace)
        self._transient_timers[namespace] = int(timer_id)
        result["ttl_ms"] = ttl_ms
        return result

    def _cancel_transient_timer(self, namespace: str) -> None:
        timer_id = self._transient_timers.pop(namespace, None)
        if timer_id is not None:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                log.debug("Transient timer %s was already removed", timer_id)

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

        if "id" in updates and str(updates["id"]) != elem_id:
            log.warning("Rejected id change for element '%s': %s", elem_id, updates["id"])
            return False

        if "type" in updates and str(updates["type"]) != str(element.elem_type):
            log.warning("Rejected type change for element '%s': %s", elem_id, updates["type"])
            return False

        merged_config = self._merge_element_config(element.config, updates)
        if element.runtime_update_requires_recreate(updates):
            if not self._recreate_element_widget(elem_id, merged_config):
                return False
            record = self.elements.get(elem_id)
            if record is None:
                return False
            element = record.element
            content_widget = element.widget
            if content_widget is None:
                return False
        else:
            element.config = merged_config

        if "opacity" in updates:
            opacity = float(updates["opacity"])
            content_widget.set_opacity(opacity)
            element.opacity = opacity
            element.config["opacity"] = opacity

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
            element.config["position"] = {"x": x, "y": y}
            element.config["size"] = {"width": width, "height": height}
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
            log.debug("Viewport size: %dx%d (source: container)", width, height)
            return width, height

        surface = self.get_surface()
        if surface is not None:
            width = max(1, int(surface.get_width()))
            height = max(1, int(surface.get_height()))
            log.debug("Viewport size: %dx%d (source: surface)", width, height)
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
                log.debug("Viewport size: %dx%d (source: monitor)", int(geometry.width), int(geometry.height))
                return int(geometry.width), int(geometry.height)

        log.debug("Viewport size: 1920x1080 (source: fallback)")
        return (1920, 1080)

    def grab_interaction(self, payload: dict) -> dict:
        if self._interaction is not None:
            self.release_interaction("replaced")

        timeout_ms = int(payload.get("timeout_ms", payload.get("ttl_ms", 10000)))
        timeout_ms = max(100, timeout_ms)
        namespace = str(payload.get("namespace", "")).strip() or None
        self._interaction = {
            "namespace": namespace,
            "callback_url": payload.get("callback_url"),
            "callback_events": payload.get("callback_events") or [],
            "correlation_id": payload.get("correlation_id"),
            "escape_releases": bool(payload.get("escape_releases", True)),
            "started_at": time.time(),
            "timeout_ms": timeout_ms,
            "last_event": None,
        }
        self.set_focusable(True)
        try:
            focused = bool(self.grab_focus())
        except Exception:
            focused = False
        self._refresh_keyboard_mode()
        self._refresh_input_region()

        def timeout_release():
            if self._interaction is not None:
                self._emit_callback("interaction.timeout", {"reason": "timeout"})
                self.release_interaction("timeout")
            return False

        self._interaction_timer = int(GLib.timeout_add(timeout_ms, timeout_release))
        return {
            "ok": True,
            "focused": focused or bool(self.has_focus()),
            "interaction": self.get_interaction_status(),
        }

    def release_interaction(self, reason: str = "released") -> dict:
        was_active = self._interaction is not None
        if self._interaction_timer is not None:
            try:
                GLib.source_remove(self._interaction_timer)
            except Exception:
                log.debug("Interaction timer %s was already removed", self._interaction_timer)
            self._interaction_timer = None
        if self._interaction is not None and reason not in {"timeout"}:
            event = "interaction.cancelled" if reason in {"escape", "cancelled"} else "interaction.focus_lost" if reason == "focus_lost" else None
            if event:
                self._emit_callback(event, {"reason": reason})
        self._interaction = None
        self._refresh_keyboard_mode()
        self._refresh_input_region()
        return {"ok": True, "was_active": was_active, "reason": reason}

    def get_interaction_status(self) -> dict:
        if self._interaction is None:
            return {"active": False}
        elapsed_ms = int((time.time() - float(self._interaction.get("started_at", time.time()))) * 1000)
        timeout_ms = int(self._interaction.get("timeout_ms", 0))
        return {
            "active": True,
            "namespace": self._interaction.get("namespace"),
            "correlation_id": self._interaction.get("correlation_id"),
            "timeout_ms": timeout_ms,
            "remaining_ms": max(0, timeout_ms - elapsed_ms),
            "escape_releases": bool(self._interaction.get("escape_releases", True)),
            "focused": bool(self.has_focus()),
        }

    def _on_interaction_key_pressed(self, _controller, keyval, keycode, state) -> bool:
        if self._interaction is None:
            return False
        name = Gdk.keyval_name(keyval) or str(keyval)
        payload = {
            "key": name,
            "keyval": int(keyval),
            "keycode": int(keycode),
            "state": int(state),
        }
        self._interaction["last_event"] = payload
        if keyval == Gdk.KEY_Escape and self._interaction.get("escape_releases", True):
            self._emit_callback("interaction.cancelled", {**payload, "reason": "escape"})
            self.release_interaction("escape")
            return True
        self._emit_callback("interaction.key_pressed", payload)
        return True

    def _on_interaction_key_released(self, _controller, keyval, keycode, state) -> bool:
        if self._interaction is None:
            return False
        self._emit_callback(
            "interaction.key_released",
            {
                "key": Gdk.keyval_name(keyval) or str(keyval),
                "keyval": int(keyval),
                "keycode": int(keycode),
                "state": int(state),
            },
        )
        return True

    def _emit_callback(self, event: str, payload: dict, record: ElementRecord | None = None) -> None:
        context = self._interaction or {}
        callback_url = context.get("callback_url")
        callback_events = context.get("callback_events") or []
        correlation_id = context.get("correlation_id")
        namespace = context.get("namespace")

        if record is not None:
            cfg = getattr(record.element, "config", {}) or {}
            callback_url = cfg.get("callback_url") or callback_url
            callback_events = cfg.get("callback_events") or callback_events
            correlation_id = cfg.get("correlation_id") or correlation_id
            namespace = record.namespace or namespace

        if not callback_url:
            return
        if callback_events and event not in set(callback_events):
            return

        body = {
            "event": event,
            "namespace": namespace,
            "correlation_id": correlation_id,
            "payload": payload,
            "timestamp": time.time(),
        }

        def post_callback():
            data = json.dumps(body).encode("utf-8")
            request = urllib.request.Request(
                str(callback_url),
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=1.5) as response:
                    response.read()
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                self._last_callback_failures.append(
                    {
                        "timestamp": time.time(),
                        "event": event,
                        "url": str(callback_url),
                        "message": str(exc),
                    },
                )
                self._last_callback_failures = self._last_callback_failures[-25:]

        threading.Thread(target=post_callback, daemon=True).start()

    def get_diagnostics(self) -> dict:
        viewport_width, viewport_height = self.get_viewport_size()
        display = Gdk.Display.get_default()
        monitors = []
        if display is not None and hasattr(display, "get_monitors"):
            model = display.get_monitors()
            for index in range(model.get_n_items()):
                monitor = model.get_item(index)
                if monitor is None:
                    continue
                geometry = monitor.get_geometry()
                monitors.append(
                    {
                        "index": index,
                        "width": int(geometry.width),
                        "height": int(geometry.height),
                        "x": int(geometry.x),
                        "y": int(geometry.y),
                    }
                )
        return {
            "namespaces": self.list_namespaces()["namespaces"],
            "elements": {
                "total": len(self.elements),
                "transient": len([record for record in self.elements.values() if record.transient]),
            },
            "transient_timers": sorted(self._transient_timers.keys()),
            "interaction": self.get_interaction_status(),
            "edit_mode": self.editor.is_edit_mode(),
            "focused": bool(self.has_focus()),
            "last_api_payloads": list(self._last_api_payloads),
            "last_callback_failures": list(self._last_callback_failures),
            "render_failures": list(self._render_failures),
            "element_failures": list(self._element_failures),
            "viewport": {"width": viewport_width, "height": viewport_height},
            "monitors": monitors,
        }

    def get_namespace_diagnostics(self, namespace: str) -> dict:
        element_ids = self._namespace_element_ids(namespace)
        return {
            "namespace": namespace,
            "element_count": len(element_ids),
            "elements": [
                {
                    "id": self.elements[elem_id].local_id or elem_id,
                    "global_id": elem_id,
                    "type": getattr(self.elements[elem_id].element, "elem_type", None),
                    "transient": self.elements[elem_id].transient,
                    "source": self.elements[elem_id].source,
                }
                for elem_id in element_ids
            ],
            "timer_active": namespace in self._transient_timers,
        }

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
            "active_profiles": self.active_profiles,
            "default": self.profile_manager.default_profile,
            "last_used": self.profile_manager.last_used_profile,
        }

    def switch_profile(self, name: str) -> dict:
        """Switch to a profile — replaces all current elements."""
        try:
            elements = self.profile_manager.load_profile(name)
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
            for elem_id in list(self.elements.keys()):
                self.remove_element(elem_id, autosave=False)

            # Add elements from profile
            for elem_cfg in elements:
                self._add_element(elem_cfg)

        self.active_profiles = [name]
        self.active_profile = name
        self._maybe_autosave_last_used()
        return {
            "ok": True,
            "active": self.active_profile,
        }

    def add_profile(self, name: str) -> dict:
        """Additively load a profile on top of current elements."""
        try:
            elements = self.profile_manager.load_profile(name)
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

        with self._suspend_autosave():
            for elem_cfg in elements:
                elem_id = elem_cfg.get("id")
                if elem_id and elem_id in self.elements:
                    pos = elem_cfg.get("position", {})
                    size = elem_cfg.get("size", {})
                    self.update_element(elem_id, {
                        "position": pos, "size": size,
                    }, autosave=False)
                else:
                    self._add_element(elem_cfg)

        if name not in self.active_profiles:
            self.active_profiles.append(name)
        self.active_profile = name
        self._maybe_autosave_last_used()
        return {
            "ok": True,
            "active": name,
            "active_profiles": self.active_profiles,
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
        """Reload config and re-load active profiles."""
        self.config = new_config
        self.base_click_through = bool(self.config.get("overlay", {}).get("click_through", True))
        self._load_interaction_config()
        self.profile_manager = LayoutProfileManager(PACKAGE_DIR, self.config)

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

        # Re-load active profiles
        with self._suspend_autosave():
            for elem_id in list(self.elements.keys()):
                self.remove_element(elem_id, autosave=False)

            try:
                profiles = self.active_profiles or self.profile_manager.startup_profiles
                elements = self.profile_manager.load_profiles(profiles)
                for elem_cfg in elements:
                    self._add_element(elem_cfg)
            except Exception:
                log.exception("Failed to reload profiles")

        self.editor.set_edit_mode(bool(self.config.get("overlay", {}).get("edit_mode", False)))
        self.editor.refresh_all()
        self._refresh_input_region()
        self._maybe_autosave_last_used()

        log.info("Config reloaded: %d elements active", len(self.elements))

    def get_elements_info(self, include_transient: bool = False) -> list[dict]:
        result = []
        for elem_id, record in self.elements.items():
            if record.transient and not include_transient:
                continue
            element = record.element
            # Start from original config for full round-trip
            entry = dict(element.config)
            if record.namespace and record.local_id:
                entry["id"] = record.local_id
                entry["namespace"] = record.namespace
            # Overlay current runtime geometry
            entry["position"] = {"x": int(element.position[0]), "y": int(element.position[1])}
            entry["size"] = {"width": int(element.size[0]), "height": int(element.size[1])}
            entry["opacity"] = float(element.opacity)
            entry["__source"] = record.source
            entry["editable"] = record.editable
            entry["transient"] = record.transient
            result.append(entry)
        return result


class HudApplication(Gtk.Application):
    """Main application managing the overlay window and API server."""

    def __init__(self):
        super().__init__(application_id="com.ckrest.desktop-hud")
        self.window: HudWindow | None = None
        self.api_thread: threading.Thread | None = None
        self.cli_profiles: list[str] | None = None
        self.cli_add_profiles: list[str] | None = None
        self.cli_no_last_used: bool = False

    def do_activate(self):
        config = load_config()

        if self.cli_profiles:
            config.setdefault("layouts", {})["startup_profiles"] = self.cli_profiles
        if self.cli_add_profiles:
            existing = config.get("layouts", {}).get("startup_profiles", ["default"])
            config.setdefault("layouts", {})["startup_profiles"] = existing + self.cli_add_profiles
        if self.cli_no_last_used:
            config.setdefault("layouts", {})["autosave_last_used"] = False

        self.window = HudWindow(self, config)
        self.window.present()

        api_cfg = config.get("api", {})
        if api_cfg.get("enabled", False):
            from desktop_hud.api import start_api_server

            port = api_cfg.get("port", 7820)
            raw_timeout = api_cfg.get("main_thread_timeout_seconds", 5.0)
            try:
                timeout_seconds = float(raw_timeout)
            except (TypeError, ValueError):
                log.warning(
                    "Invalid api.main_thread_timeout_seconds=%r; falling back to 5.0",
                    raw_timeout,
                )
                timeout_seconds = 5.0
            if timeout_seconds <= 0:
                log.warning(
                    "Invalid api.main_thread_timeout_seconds=%s; falling back to 5.0",
                    timeout_seconds,
                )
                timeout_seconds = 5.0
            self.api_thread = threading.Thread(
                target=start_api_server,
                args=(self.window, port, timeout_seconds),
                daemon=True,
            )
            self.api_thread.start()
            log.info(
                "API server started on port %d with dispatch timeout %.2fs",
                port,
                timeout_seconds,
            )

        log.info("Desktop HUD started with %d elements", len(self.window.elements))

    def reload(self):
        """Reload config (called from SIGHUP handler)."""
        if self.window is None:
            return

        try:
            config = load_config()
            self.window.reload_config(config)
        except Exception:
            log.exception("Config reload failed")


def main(argv: list[str] | None = None):
    import argparse

    parser = argparse.ArgumentParser(description="Desktop HUD overlay")
    parser.add_argument("--profile", nargs="*", help="Startup profile(s) to load (overrides config)")
    parser.add_argument("--add", nargs="*", help="Additional profile(s) to load on top")
    parser.add_argument("--no-last-used", action="store_true", help="Skip restoring last-used geometry")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    app = HudApplication()
    app.cli_profiles = args.profile
    app.cli_add_profiles = args.add
    app.cli_no_last_used = args.no_last_used

    # SIGHUP triggers config reload.
    def on_sighup(*_args):
        GLib.idle_add(app.reload)

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, on_sighup)

    app.run(None)
