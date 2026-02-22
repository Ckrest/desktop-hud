"""Graph-lib integration for HUD elements."""

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from desktop_hud.elements.base import HudElement

log = logging.getLogger(__name__)

# Renderer and provider factories
RENDERERS = {}
PROVIDERS = {}


def _ensure_graph_lib():
    """Import graph-lib via systems dependency resolution."""
    global RENDERERS, PROVIDERS
    if RENDERERS:
        return True

    try:
        from systems import ensure_importable

        if not ensure_importable("graph-lib"):
            log.error("graph-lib not importable via systems")
            return False
    except ImportError:
        log.warning("systems package not available, trying direct import")

    try:
        from graph_lib import LineChartRenderer, GaugeRenderer, GraphWidget
        from graph_lib.providers import CommandProvider
        from graph_lib import StaticProvider, DataPoint

        RENDERERS.update({
            "line": LineChartRenderer,
            "gauge": GaugeRenderer,
        })
        PROVIDERS.update({
            "command": _create_command_provider,
            "static": _create_static_provider,
        })
        return True
    except ImportError:
        log.exception("Failed to import graph-lib")
        return False


def _create_command_provider(provider_cfg: dict):
    from graph_lib.providers import CommandProvider

    return CommandProvider(
        command=provider_cfg["command"],
        parse_mode=provider_cfg.get("parse_mode", "float"),
        history_seconds=provider_cfg.get("history_seconds", 60),
        poll_interval_ms=provider_cfg.get("interval_ms", 1000),
    )


def _create_static_provider(provider_cfg: dict):
    from graph_lib import StaticProvider, DataPoint

    value = provider_cfg.get("value", 0)
    return StaticProvider(data=[DataPoint(timestamp=0, value=float(value))])


class GraphElement(HudElement):
    """Graph-lib widget embedded in the HUD overlay."""

    def create_widget(self) -> Gtk.Widget:
        if not _ensure_graph_lib():
            raise RuntimeError("graph-lib is not available")

        from graph_lib import GraphWidget

        renderer_name = self.config.get("renderer", "gauge")
        renderer_cls = RENDERERS.get(renderer_name)
        if renderer_cls is None:
            raise ValueError(f"Unknown renderer: {renderer_name}")

        renderer = renderer_cls()
        renderer_config = self.config.get("renderer_config", {})
        if renderer_config:
            renderer.configure(**renderer_config)

        provider_cfg = self.config.get("provider", {})
        provider_type = provider_cfg.get("type", "static")
        provider_factory = PROVIDERS.get(provider_type)
        if provider_factory is None:
            raise ValueError(f"Unknown provider type: {provider_type}")

        provider = provider_factory(provider_cfg)

        refresh_ms = provider_cfg.get("interval_ms", 1000)
        graph_widget = GraphWidget(
            renderer,
            provider,
            refresh_interval_ms=refresh_ms,
            clear_before_draw=True,
        )
        graph_widget.set_size_request(*self.size)
        graph_widget.start()

        self._graph_widget = graph_widget
        self.widget = graph_widget
        log.info("Graph element '%s' (renderer=%s) created", self.id, renderer_name)
        return graph_widget

    def destroy(self):
        if hasattr(self, "_graph_widget") and self._graph_widget is not None:
            self._graph_widget.stop()
            self._graph_widget = None
        super().destroy()
