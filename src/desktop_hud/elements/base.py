"""Abstract base class for HUD elements."""

from abc import ABC, abstractmethod
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from desktop_hud.config import PACKAGE_DIR


class ElementSkipRequested(Exception):
    """Raised when element config requests skip behavior for source failures."""


class HudElement(ABC):
    """Base class for all HUD overlay elements."""

    def __init__(self, config: dict):
        self.id: str = config["id"]
        self.elem_type: str = config["type"]

        pos = config.get("position", {})
        self.position: tuple[int, int] = (pos.get("x", 0), pos.get("y", 0))

        size = config.get("size", {})
        self.size: tuple[int, int] = (size.get("width", 100), size.get("height", 100))

        self.opacity: float = config.get("opacity", 1.0)
        self.config = config
        self.widget: Gtk.Widget | None = None

    @abstractmethod
    def create_widget(self) -> Gtk.Widget | None:
        """Create and return the GTK widget for this element."""
        ...

    def runtime_update_requires_recreate(self, updates: dict) -> bool:
        """Whether a runtime PATCH requires widget recreation for this element type."""
        return False

    def resolve_source_path(self, source: str) -> Path:
        """Resolve source path from config.

        Relative paths are resolved against the package root.
        """
        raw = Path(str(source).strip()).expanduser()
        if raw.is_absolute():
            return raw
        return (PACKAGE_DIR / raw).resolve()

    def get_missing_source_policy(self) -> str:
        policy = str(self.config.get("on_missing_source", "error")).strip().lower()
        if policy not in {"error", "skip", "placeholder"}:
            return "error"
        return policy

    def _build_placeholder_widget(self, title: str, detail: str) -> Gtk.Widget:
        """Build a generic placeholder widget for missing media sources."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("hud-placeholder")
        box.set_valign(Gtk.Align.FILL)
        box.set_halign(Gtk.Align.FILL)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        title_label = Gtk.Label(label=title)
        title_label.add_css_class("hud-placeholder-title")
        title_label.set_wrap(True)
        title_label.set_xalign(0.0)
        box.append(title_label)

        detail_label = Gtk.Label(label=detail)
        detail_label.add_css_class("hud-placeholder-detail")
        detail_label.set_wrap(True)
        detail_label.set_xalign(0.0)
        box.append(detail_label)
        return box

    def handle_source_error(self, kind: str, detail: str) -> Gtk.Widget | None:
        """Handle source errors with error/skip/placeholder policy."""
        policy = self.get_missing_source_policy()
        if policy == "placeholder":
            title_default = f"{self.elem_type.upper()} unavailable"
            title = str(self.config.get("placeholder_label", title_default)).strip() or title_default
            return self._build_placeholder_widget(title, detail)
        if policy == "skip":
            raise ElementSkipRequested(detail)
        if kind in {"missing_source", "empty_source", "invalid_source"}:
            raise FileNotFoundError(detail)
        raise RuntimeError(detail)

    def destroy(self):
        """Clean up the element and its widget."""
        if self.widget is not None:
            parent = self.widget.get_parent()
            if parent is not None and isinstance(parent, Gtk.Fixed):
                parent.remove(self.widget)
            self.widget = None
