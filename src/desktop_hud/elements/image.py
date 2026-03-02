"""Static image HUD element."""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk, GLib

from desktop_hud.elements.base import HudElement

log = logging.getLogger(__name__)


class ImageElement(HudElement):
    """Displays a static image (PNG, SVG, etc.) on the overlay."""

    RECREATE_FIELDS = {"source", "on_missing_source", "placeholder_label"}

    def runtime_update_requires_recreate(self, updates: dict) -> bool:
        return any(key in self.RECREATE_FIELDS for key in updates)

    def create_widget(self) -> Gtk.Widget | None:
        source = str(self.config.get("source", "")).strip()
        if not source:
            widget = self.handle_source_error(
                kind="empty_source",
                detail=f"Image element '{self.id}' has an empty source path",
            )
            if widget is not None:
                self.widget = widget
            return widget

        w, h = self.size
        path = self.resolve_source_path(source)

        if not path.exists():
            widget = self.handle_source_error(
                kind="missing_source",
                detail=f"Image source does not exist: {path}",
            )
            if widget is not None:
                self.widget = widget
            return widget

        if not path.is_file():
            widget = self.handle_source_error(
                kind="invalid_source",
                detail=f"Image source is not a regular file: {path}",
            )
            if widget is not None:
                self.widget = widget
            return widget

        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(path), w, h, True,
            )
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_size_request(w, h)
            picture.set_can_shrink(True)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        except GLib.Error as exc:
            widget = self.handle_source_error(
                kind="decode_error",
                detail=f"Image source failed to load ({path}): {exc.message}",
            )
            if widget is not None:
                self.widget = widget
            return widget

        self.widget = picture
        log.info("Image element '%s' loaded from %s", self.id, path)
        return picture
