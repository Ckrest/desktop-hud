"""Viewport-aware frame and scene layout helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedFrame:
    x: int
    y: int
    width: int
    height: int

    def to_data(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


ANCHORS = {
    "top-left": (0.0, 0.0),
    "top-center": (0.5, 0.0),
    "top-right": (1.0, 0.0),
    "center-left": (0.0, 0.5),
    "center": (0.5, 0.5),
    "center-right": (1.0, 0.5),
    "bottom-left": (0.0, 1.0),
    "bottom-center": (0.5, 1.0),
    "bottom-right": (1.0, 1.0),
}


def parse_dimension(value: Any, viewport_width: int, viewport_height: int, axis: str = "x", basis: int | None = None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value))

    text = str(value).strip().lower()
    if not text or text == "auto":
        return None
    if text.endswith("px"):
        return int(round(float(text[:-2])))
    if text.endswith("vw"):
        return int(round(viewport_width * float(text[:-2]) / 100.0))
    if text.endswith("vh"):
        return int(round(viewport_height * float(text[:-2]) / 100.0))
    if text.endswith("%"):
        base = basis if basis is not None else (viewport_width if axis == "x" else viewport_height)
        return int(round(base * float(text[:-1]) / 100.0))
    return int(round(float(text)))


def resolve_margin(value: Any, viewport_width: int, viewport_height: int) -> dict[str, int]:
    if value is None:
        return {"top": 0, "right": 0, "bottom": 0, "left": 0}
    if not isinstance(value, dict):
        margin = parse_dimension(value, viewport_width, viewport_height, "x") or 0
        return {"top": margin, "right": margin, "bottom": margin, "left": margin}

    x = parse_dimension(value.get("x"), viewport_width, viewport_height, "x") or 0
    y = parse_dimension(value.get("y"), viewport_width, viewport_height, "y") or 0
    return {
        "top": parse_dimension(value.get("top"), viewport_width, viewport_height, "y") if value.get("top") is not None else y,
        "right": parse_dimension(value.get("right"), viewport_width, viewport_height, "x") if value.get("right") is not None else x,
        "bottom": parse_dimension(value.get("bottom"), viewport_width, viewport_height, "y") if value.get("bottom") is not None else y,
        "left": parse_dimension(value.get("left"), viewport_width, viewport_height, "x") if value.get("left") is not None else x,
    }


def _offset(frame: dict[str, Any], viewport_width: int, viewport_height: int) -> tuple[int, int]:
    raw = frame.get("offset") or {}
    if not isinstance(raw, dict):
        return 0, 0
    return (
        parse_dimension(raw.get("x", 0), viewport_width, viewport_height, "x") or 0,
        parse_dimension(raw.get("y", 0), viewport_width, viewport_height, "y") or 0,
    )


def resolve_frame(
    frame: dict[str, Any],
    viewport_width: int,
    viewport_height: int,
    *,
    default_width: int = 100,
    default_height: int = 100,
) -> ResolvedFrame:
    frame = frame or {}
    margin = resolve_margin(frame.get("margin"), viewport_width, viewport_height)
    available_width = max(1, viewport_width - margin["left"] - margin["right"])
    available_height = max(1, viewport_height - margin["top"] - margin["bottom"])

    width = parse_dimension(frame.get("width"), viewport_width, viewport_height, "x") or default_width
    height = parse_dimension(frame.get("height"), viewport_width, viewport_height, "y") or default_height

    min_width = parse_dimension(frame.get("min_width"), viewport_width, viewport_height, "x")
    max_width = parse_dimension(frame.get("max_width"), viewport_width, viewport_height, "x")
    min_height = parse_dimension(frame.get("min_height"), viewport_width, viewport_height, "y")
    max_height = parse_dimension(frame.get("max_height"), viewport_width, viewport_height, "y")

    if min_width is not None:
        width = max(width, min_width)
    if max_width is not None:
        width = min(width, max_width)
    if min_height is not None:
        height = max(height, min_height)
    if max_height is not None:
        height = min(height, max_height)

    width = min(max(1, width), available_width)
    height = min(max(1, height), available_height)

    anchor_name = str(frame.get("anchor", "top-left")).strip().lower()
    origin_name = str(frame.get("origin", anchor_name)).strip().lower()
    anchor = ANCHORS.get(anchor_name, ANCHORS["top-left"])
    origin = ANCHORS.get(origin_name, ANCHORS.get(anchor_name, ANCHORS["top-left"]))
    offset_x, offset_y = _offset(frame, viewport_width, viewport_height)

    anchor_x = margin["left"] + anchor[0] * available_width
    anchor_y = margin["top"] + anchor[1] * available_height
    x = int(round(anchor_x - origin[0] * width + offset_x))
    y = int(round(anchor_y - origin[1] * height + offset_y))

    min_x = margin["left"]
    max_x = viewport_width - margin["right"] - width
    min_y = margin["top"]
    max_y = viewport_height - margin["bottom"] - height
    x = min(max(min_x, x), max(min_x, max_x))
    y = min(max(min_y, y), max(min_y, max_y))
    return ResolvedFrame(x=x, y=y, width=width, height=height)


def resolve_stack_scene(payload: dict[str, Any], viewport_width: int, viewport_height: int) -> tuple[list[dict[str, Any]], ResolvedFrame]:
    layout = payload.get("layout") or {}
    direction = str(layout.get("direction", "vertical")).strip().lower()
    if str(layout.get("type", "stack")).strip().lower() != "stack" or direction != "vertical":
        raise ValueError("Only vertical stack scenes are supported")

    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise ValueError("Scene items must be a non-empty list")

    frame = dict(payload.get("frame") or {})
    parent_width = parse_dimension(frame.get("width"), viewport_width, viewport_height, "x") or viewport_width
    min_width = parse_dimension(frame.get("min_width"), viewport_width, viewport_height, "x")
    max_width = parse_dimension(frame.get("max_width"), viewport_width, viewport_height, "x")
    if min_width is not None:
        parent_width = max(parent_width, min_width)
    if max_width is not None:
        parent_width = min(parent_width, max_width)

    gap = parse_dimension(layout.get("gap", 0), viewport_width, viewport_height, "y") or 0
    child_sizes: list[tuple[int, int]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Scene item must be an object")
        width = parse_dimension(item.get("width"), viewport_width, viewport_height, "x", basis=parent_width) or parent_width
        height = parse_dimension(item.get("height"), viewport_width, viewport_height, "y") or 100
        child_sizes.append((min(width, parent_width), max(1, height)))

    total_height = sum(height for _, height in child_sizes) + gap * (len(child_sizes) - 1)
    if "height" not in frame:
        frame["height"] = total_height
    parent = resolve_frame(frame, viewport_width, viewport_height, default_width=parent_width, default_height=total_height)

    align = str(layout.get("align", "center")).strip().lower()
    y = parent.y
    elements: list[dict[str, Any]] = []
    for item, (width, height) in zip(items, child_sizes):
        if align == "end":
            x = parent.x + parent.width - width
        elif align == "start":
            x = parent.x
        else:
            x = parent.x + int(round((parent.width - width) / 2))
        resolved = ResolvedFrame(x=x, y=y, width=width, height=height)
        cfg = dict(item)
        cfg["resolved_frame"] = resolved.to_data()
        cfg["frame"] = {
            "anchor": "top-left",
            "origin": "top-left",
            "offset": {"x": f"{x}px", "y": f"{y}px"},
            "width": f"{width}px",
            "height": f"{height}px",
        }
        elements.append(cfg)
        y += height + gap
    return elements, parent
