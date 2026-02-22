"""Static image HUD element."""

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from desktop_hud.elements.base import HudElement

log = logging.getLogger(__name__)


class ImageElement(HudElement):
    """Displays a static image (PNG, SVG, etc.) on the overlay."""

    def create_widget(self) -> Gtk.Widget:
        source = self.config.get("source", "")
        path = Path(source).expanduser()

        w, h = self.size

        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(path), w, h, True,
        )
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        picture = Gtk.Picture.new_for_paintable(texture)
        picture.set_size_request(w, h)
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        self.widget = picture
        log.info("Image element '%s' loaded from %s", self.id, path)
        return picture
