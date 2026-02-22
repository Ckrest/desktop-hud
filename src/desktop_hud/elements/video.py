"""Video HUD element with optional alpha-channel support."""

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from desktop_hud.elements.base import HudElement

log = logging.getLogger(__name__)


class VideoElement(HudElement):
    """Displays a video on the overlay. Supports alpha-channel video via GStreamer."""

    def create_widget(self) -> Gtk.Widget:
        source = str(self.config.get("source", "")).strip()
        if not source:
            raise ValueError(f"Video element '{self.id}' has an empty source path")

        path = Path(source).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Video source does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"Video source is not a regular file: {path}")

        loop = self.config.get("loop", True)
        use_alpha = self.config.get("alpha", False)

        self._probe_media_source(path)

        if use_alpha:
            widget = self._create_alpha_widget(path, loop)
        else:
            widget = self._create_simple_widget(path, loop)

        self.widget = widget
        return widget

    def _create_simple_widget(self, path: Path, loop: bool) -> Gtk.Widget:
        """Simple video playback via Gtk.Video."""
        media = Gtk.MediaFile.new_for_filename(str(path))
        media.connect("notify::error", self._on_media_error, path)

        video = Gtk.Video.new_for_media_stream(media)
        video.set_autoplay(True)
        video.set_loop(loop)
        video.set_size_request(*self.size)

        self._media_stream = media
        log.info("Video element '%s' (simple) loaded from %s", self.id, path)
        return video

    def _on_media_error(self, media_stream: Gtk.MediaStream, _pspec, path: Path):
        error = media_stream.get_error()
        if error is None:
            return
        log.error(
            "Video element '%s' playback error for %s: %s",
            self.id,
            path,
            error.message,
        )

    def _probe_media_source(self, path: Path):
        """Fail fast with actionable messages before creating a video widget."""
        try:
            gi.require_version("Gst", "1.0")
            gi.require_version("GstPbutils", "1.0")
            from gi.repository import Gst, GstPbutils
        except ValueError:
            # If Gst typelibs are unavailable we still let GTK try playback.
            return

        Gst.init(None)
        discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
        uri = GLib.filename_to_uri(str(path.resolve()), None)

        try:
            discoverer.discover_uri(uri)
        except GLib.Error as err:
            message = err.message
            if "missing a plug-in" in message.lower():
                message = (
                    f"{message} Install codec plugins such as "
                    "gstreamer1.0-libav, gstreamer1.0-plugins-bad, and "
                    "gstreamer1.0-plugins-ugly."
                )
            raise RuntimeError(message) from err

    def _create_alpha_widget(self, path: Path, loop: bool) -> Gtk.Widget:
        """Alpha-channel video via native gtk4paintablesink."""
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)

        if Gst.ElementFactory.find("gtk4paintablesink") is None:
            raise RuntimeError(
                "Alpha video requires GStreamer element 'gtk4paintablesink'. "
                "Install the GTK4 GStreamer sink plugin and verify with: "
                "gst-inspect-1.0 gtk4paintablesink"
            )

        pipeline_str = (
            f'filesrc location="{path}" ! decodebin ! videoconvert ! '
            f"gtk4paintablesink name=sink"
        )
        pipeline = Gst.parse_launch(pipeline_str)
        sink = pipeline.get_by_name("sink")
        if sink is None:
            raise RuntimeError("Failed to initialize gtk4paintablesink in alpha pipeline")

        picture = Gtk.Picture.new()
        picture.set_size_request(*self.size)
        bind_state = {"tries": 0}

        def bind_paintable():
            paintable = sink.get_property("paintable")
            if paintable is not None:
                picture.set_paintable(paintable)
                return False
            bind_state["tries"] += 1
            if bind_state["tries"] >= 200:
                log.warning(
                    "Video element '%s' could not bind gtk4paintablesink paintable",
                    self.id,
                )
                return False
            return True

        GLib.timeout_add(25, bind_paintable)

        bus = pipeline.get_bus()
        bus.add_signal_watch()

        def on_message(bus, msg):
            if msg.type == Gst.MessageType.EOS and loop:
                pipeline.seek_simple(
                    Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0,
                )
            elif msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                if debug:
                    log.error(
                        "Video element '%s' (alpha) pipeline error for %s: %s (%s)",
                        self.id,
                        path,
                        err.message,
                        debug,
                    )
                else:
                    log.error(
                        "Video element '%s' (alpha) pipeline error for %s: %s",
                        self.id,
                        path,
                        err.message,
                    )

        bus.connect("message", on_message)

        pipeline.set_state(Gst.State.PLAYING)
        log.info("Video element '%s' (alpha) loaded from %s", self.id, path)

        # Store pipeline reference for cleanup
        self._pipeline = pipeline
        self._pipeline_bus = bus
        return picture

    def destroy(self):
        if hasattr(self, "_pipeline_bus") and self._pipeline_bus is not None:
            self._pipeline_bus.remove_signal_watch()
            self._pipeline_bus = None
        if hasattr(self, "_pipeline") and self._pipeline is not None:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        super().destroy()
