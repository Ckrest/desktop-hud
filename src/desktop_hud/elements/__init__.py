"""HUD element types."""

from desktop_hud.elements.base import HudElement, ElementSkipRequested
from desktop_hud.elements.image import ImageElement
from desktop_hud.elements.video import VideoElement
from desktop_hud.elements.graph import GraphElement
from desktop_hud.elements.generic import (
    KeyboardElement,
    ListElement,
    PanelElement,
    TableElement,
    TextElement,
    ToastElement,
)

ELEMENT_TYPES = {
    "image": ImageElement,
    "video": VideoElement,
    "graph": GraphElement,
    "text": TextElement,
    "list": ListElement,
    "panel": PanelElement,
    "toast": ToastElement,
    "table": TableElement,
    "keyboard": KeyboardElement,
}

__all__ = [
    "HudElement",
    "ElementSkipRequested",
    "ImageElement",
    "VideoElement",
    "GraphElement",
    "TextElement",
    "ListElement",
    "PanelElement",
    "ToastElement",
    "TableElement",
    "KeyboardElement",
    "ELEMENT_TYPES",
]
