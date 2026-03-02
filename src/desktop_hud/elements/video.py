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

    VALID_BACKENDS = {"auto", "simple", "alpha-pipeline"}
    RECREATE_FIELDS = {
        "source",
        "loop",
        "alpha",
        "backend",
        "on_missing_source",
        "placeholder_label",
    }

    def runtime_update_requires_recreate(self, updates: dict) -> bool:
        return any(key in self.RECREATE_FIELDS for key in updates)

    def create_widget(self) -> Gtk.Widget | None:
        source = str(self.config.get("source", "")).strip()
        if not source:
            widget = self.handle_source_error(
                kind="empty_source",
                detail=f"Video element '{self.id}' has an empty source path",
            )
            if widget is not None:
                self.widget = widget
            return widget

        path = self.resolve_source_path(source)
        if not path.exists():
            widget = self.handle_source_error(
                kind="missing_source",
                detail=f"Video source does not exist: {path}",
            )
            if widget is not None:
                self.widget = widget
            return widget
        if not path.is_file():
            widget = self.handle_source_error(
                kind="invalid_source",
                detail=f"Video source is not a regular file: {path}",
            )
            if widget is not None:
                self.widget = widget
            return widget

        loop = self._as_bool(self.config.get("loop", True), default=True)
        use_alpha = self._as_bool(self.config.get("alpha", False), default=False)

        try:
            probe_info = self._probe_media_source(path)
            backend = self._select_backend(use_alpha)
            requested_backend = str(self.config.get("backend", "auto")).strip().lower() or "auto"

            log.info(
                "Video element '%s' selecting backend=%s (requested=%s, alpha=%s, loop=%s, probe=%s)",
                self.id,
                backend,
                requested_backend,
                use_alpha,
                loop,
                probe_info,
            )

            if backend == "alpha-pipeline":
                try:
                    widget = self._create_alpha_widget(path, loop)
                except Exception:
                    if requested_backend == "auto":
                        log.warning(
                            "Video element '%s' alpha backend failed in auto mode; falling back to simple backend",
                            self.id,
                            exc_info=True,
                        )
                        widget = self._create_simple_widget(path, loop)
                        backend = "simple"
                    else:
                        raise
            else:
                widget = self._create_simple_widget(path, loop)
        except Exception as exc:
            widget = self.handle_source_error(
                kind="decode_error",
                detail=f"Video source failed to load ({path}): {exc}",
            )
            if widget is not None:
                self.widget = widget
            return widget

        self.widget = widget
        self._active_backend = backend
        return widget

    @staticmethod
    def _as_bool(value, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return default

    def _select_backend(self, use_alpha: bool) -> str:
        requested = str(self.config.get("backend", "auto")).strip().lower() or "auto"
        if requested not in self.VALID_BACKENDS:
            choices = ", ".join(sorted(self.VALID_BACKENDS))
            raise ValueError(f"Invalid video backend '{requested}'. Valid values: {choices}")

        if requested == "auto":
            return "alpha-pipeline" if use_alpha else "simple"

        if requested == "simple" and use_alpha:
            log.warning(
                "Video element '%s' has alpha=true with backend=simple; transparency will not be preserved",
                self.id,
            )
        return requested

    def _create_simple_widget(self, path: Path, loop: bool) -> Gtk.Widget:
        """Simple video playback via Gtk.Video."""
        media = Gtk.MediaFile.new_for_filename(str(path))
        try:
            media.set_loop(loop)
        except Exception:
            log.debug("Video element '%s': Gtk.MediaFile.set_loop unavailable", self.id)

        error_handler_id = media.connect("notify::error", self._on_media_error, path)
        ended_handler_id = media.connect("notify::ended", self._on_media_ended, path)

        video = Gtk.Video.new_for_media_stream(media)
        video.set_autoplay(True)
        video.set_loop(loop)
        video.set_size_request(*self.size)

        self._media_stream = media
        self._media_source_path = path
        self._media_signal_ids = [error_handler_id, ended_handler_id]
        self._simple_loop_enabled = bool(loop)
        self._simple_loop_fail_count = 0
        self._simple_loop_watchdog_id = None
        if loop:
            # Some GTK/GStreamer combinations occasionally ignore loop=true for specific files.
            # Keep a lightweight fallback watchdog that restarts ended streams.
            self._simple_loop_watchdog_id = GLib.timeout_add(300, self._simple_loop_watchdog_tick)

        log.info("Video element '%s' (simple, loop=%s) loaded from %s", self.id, loop, path)
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

    def _on_media_ended(self, media_stream: Gtk.MediaStream, _pspec, path: Path):
        if not getattr(self, "_simple_loop_enabled", False):
            return

        try:
            ended = bool(media_stream.get_ended())
        except Exception:
            ended = False

        if not ended:
            return

        if self._restart_media_stream(media_stream):
            log.debug(
                "Video element '%s' restarted simple loop after end-of-stream: %s",
                self.id,
                path,
            )
        else:
            log.warning(
                "Video element '%s' could not restart simple loop stream: %s",
                self.id,
                path,
            )

    @staticmethod
    def _restart_media_stream(media_stream: Gtk.MediaStream) -> bool:
        try:
            media_stream.seek(0)
            media_stream.play()
            return True
        except Exception:
            return False

    def _simple_loop_watchdog_tick(self) -> bool:
        media_stream = getattr(self, "_media_stream", None)
        if media_stream is None or not getattr(self, "_simple_loop_enabled", False):
            return False

        try:
            ended = bool(media_stream.get_ended())
        except Exception:
            ended = False

        if not ended:
            return True

        if self._restart_media_stream(media_stream):
            if getattr(self, "_simple_loop_fail_count", 0) > 0:
                log.info("Video element '%s' simple loop watchdog recovered stream", self.id)
                self._simple_loop_fail_count = 0
            return True

        self._simple_loop_fail_count = getattr(self, "_simple_loop_fail_count", 0) + 1
        if self._simple_loop_fail_count in {1, 5, 20}:
            log.warning(
                "Video element '%s' simple loop watchdog restart failed (%d attempts)",
                self.id,
                self._simple_loop_fail_count,
            )
        return True

    def _probe_media_source(self, path: Path) -> dict:
        """Fail fast with actionable messages before creating a video widget."""
        probe = {
            "probed": False,
            "path": str(path),
        }
        try:
            gi.require_version("Gst", "1.0")
            gi.require_version("GstPbutils", "1.0")
            from gi.repository import Gst, GstPbutils
        except ValueError:
            # If Gst typelibs are unavailable we still let GTK try playback.
            probe["reason"] = "missing_gstreamer_typelibs"
            return probe

        Gst.init(None)
        discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
        uri = GLib.filename_to_uri(str(path.resolve()), None)

        try:
            info = discoverer.discover_uri(uri)
        except GLib.Error as err:
            message = err.message
            if "missing a plug-in" in message.lower():
                message = (
                    f"{message} Install codec plugins such as "
                    "gstreamer1.0-libav, gstreamer1.0-plugins-bad, and "
                    "gstreamer1.0-plugins-ugly."
                )
            raise RuntimeError(message) from err
        probe["probed"] = True
        probe["uri"] = uri

        try:
            duration_ns = int(info.get_duration())
            if duration_ns > 0:
                probe["duration_seconds"] = round(duration_ns / float(Gst.SECOND), 3)
        except Exception:
            pass

        def _stream_count(streams) -> int | None:
            if streams is None:
                return 0
            try:
                return len(streams)
            except Exception:
                try:
                    return int(streams.get_length())
                except Exception:
                    return None

        try:
            probe["video_streams"] = _stream_count(info.get_video_streams())
        except Exception:
            pass
        try:
            probe["audio_streams"] = _stream_count(info.get_audio_streams())
        except Exception:
            pass
        return probe

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
            f'videoscale method=0 ! '
            f'video/x-raw,width={self.size[0]},height={self.size[1]},format=RGBA ! '
            f"gtk4paintablesink name=sink"
        )
        pipeline = Gst.parse_launch(pipeline_str)
        sink = pipeline.get_by_name("sink")
        if sink is None:
            raise RuntimeError("Failed to initialize gtk4paintablesink in alpha pipeline")

        picture = Gtk.Picture.new()
        picture.set_size_request(*self.size)
        picture.set_can_shrink(False)
        # Try to disable aspect ratio maintenance
        try:
            picture.set_keep_aspect_ratio(False)
        except (AttributeError, TypeError):
            # Property may not exist in this GTK version
            pass
        bind_state = {"tries": 0}

        def bind_paintable():
            paintable = sink.get_property("paintable")
            if paintable is not None:
                picture.set_paintable(paintable)
                # Force the paintable to fill the entire allocated space
                try:
                    picture.set_content_fit(Gtk.ContentFit.FILL)
                except (AttributeError, TypeError):
                    # Gtk.ContentFit may not be available in this GTK version
                    try:
                        # Alternative: try to set halign/valign to fill
                        picture.set_halign(Gtk.Align.FILL)
                        picture.set_valign(Gtk.Align.FILL)
                    except:
                        pass
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
        if hasattr(self, "_simple_loop_watchdog_id") and self._simple_loop_watchdog_id is not None:
            try:
                GLib.source_remove(self._simple_loop_watchdog_id)
            except Exception:
                pass
            self._simple_loop_watchdog_id = None

        if hasattr(self, "_media_stream") and self._media_stream is not None:
            signal_ids = getattr(self, "_media_signal_ids", []) or []
            for signal_id in signal_ids:
                try:
                    self._media_stream.disconnect(signal_id)
                except Exception:
                    pass
            self._media_signal_ids = []

        if hasattr(self, "_pipeline_bus") and self._pipeline_bus is not None:
            self._pipeline_bus.remove_signal_watch()
            self._pipeline_bus = None
        if hasattr(self, "_pipeline") and self._pipeline is not None:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        super().destroy()
