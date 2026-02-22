"""Abstract base class for HUD elements."""

from abc import ABC, abstractmethod

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


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
    def create_widget(self) -> Gtk.Widget:
        """Create and return the GTK widget for this element."""
        ...

    def destroy(self):
        """Clean up the element and its widget."""
        if self.widget is not None:
            parent = self.widget.get_parent()
            if parent is not None and isinstance(parent, Gtk.Fixed):
                parent.remove(self.widget)
            self.widget = None
