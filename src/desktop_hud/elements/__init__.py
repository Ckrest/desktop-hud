"""HUD element types."""

from desktop_hud.elements.base import HudElement, ElementSkipRequested
from desktop_hud.elements.image import ImageElement
from desktop_hud.elements.video import VideoElement
from desktop_hud.elements.graph import GraphElement

ELEMENT_TYPES = {
    "image": ImageElement,
    "video": VideoElement,
    "graph": GraphElement,
}

__all__ = [
    "HudElement",
    "ElementSkipRequested",
    "ImageElement",
    "VideoElement",
    "GraphElement",
    "ELEMENT_TYPES",
]
