"""Geometry helpers for moving/resizing HUD elements with snapping."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """Simple rectangle helper used by editor interactions."""

    x: int
    y: int
    width: int
    height: int

    @property
    def left(self) -> int:
        return self.x

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def top(self) -> int:
        return self.y

    @property
    def bottom(self) -> int:
        return self.y + self.height


def clamp_move_rect(rect: Rect, bounds_width: int, bounds_height: int) -> Rect:
    """Clamp a rectangle so it remains fully inside the viewport."""
    max_x = max(0, bounds_width - rect.width)
    max_y = max(0, bounds_height - rect.height)
    x = min(max(0, rect.x), max_x)
    y = min(max(0, rect.y), max_y)
    return Rect(x=x, y=y, width=rect.width, height=rect.height)


def _nearest(value: int, candidates: list[int], threshold: int) -> int:
    best = value
    best_delta = threshold + 1

    for candidate in candidates:
        delta = abs(value - candidate)
        if delta <= threshold and delta < best_delta:
            best = candidate
            best_delta = delta

    return best


def _move_x_candidates(rect: Rect, others: list[Rect], bounds_width: int) -> list[int]:
    candidates = [0, bounds_width - rect.width]

    for other in others:
        # Align left edge to other edges.
        candidates.append(other.left)
        candidates.append(other.right)
        # Align right edge to other edges.
        candidates.append(other.left - rect.width)
        candidates.append(other.right - rect.width)

    return candidates


def _move_y_candidates(rect: Rect, others: list[Rect], bounds_height: int) -> list[int]:
    candidates = [0, bounds_height - rect.height]

    for other in others:
        # Align top edge to other edges.
        candidates.append(other.top)
        candidates.append(other.bottom)
        # Align bottom edge to other edges.
        candidates.append(other.top - rect.height)
        candidates.append(other.bottom - rect.height)

    return candidates


def snap_move_rect(
    proposed: Rect,
    others: list[Rect],
    bounds_width: int,
    bounds_height: int,
    threshold: int,
) -> Rect:
    """Snap a moved rectangle to viewport edges and sibling element edges."""
    clamped = clamp_move_rect(proposed, bounds_width, bounds_height)

    snapped_x = _nearest(
        clamped.x,
        _move_x_candidates(clamped, others, bounds_width),
        threshold,
    )
    snapped_y = _nearest(
        clamped.y,
        _move_y_candidates(clamped, others, bounds_height),
        threshold,
    )

    return clamp_move_rect(
        Rect(x=snapped_x, y=snapped_y, width=clamped.width, height=clamped.height),
        bounds_width,
        bounds_height,
    )


def _apply_aspect_ratio(
    left: int,
    top: int,
    right: int,
    bottom: int,
    handle: str,
    ratio: float,
) -> tuple[int, int, int, int]:
    width = max(1, right - left)
    height = max(1, bottom - top)

    if width / height > ratio:
        # Too wide for the target ratio.
        target_width = int(round(height * ratio))
        if handle in ("ne", "se"):
            right = left + target_width
        else:
            left = right - target_width
    else:
        # Too tall for the target ratio.
        target_height = int(round(width / ratio))
        if handle in ("sw", "se"):
            bottom = top + target_height
        else:
            top = bottom - target_height

    return left, top, right, bottom


def snap_resize_rect(
    proposed: Rect,
    start: Rect,
    handle: str,
    others: list[Rect],
    bounds_width: int,
    bounds_height: int,
    threshold: int,
    min_width: int,
    min_height: int,
    lock_aspect: bool,
) -> Rect:
    """Snap a resized rectangle while keeping the opposite corner fixed."""
    left = proposed.left
    top = proposed.top
    right = proposed.right
    bottom = proposed.bottom

    fixed_left = start.left
    fixed_top = start.top
    fixed_right = start.right
    fixed_bottom = start.bottom

    # Maintain anchor edges from the start rect.
    if handle in ("ne", "se"):
        left = fixed_left
    if handle in ("nw", "sw"):
        right = fixed_right
    if handle in ("sw", "se"):
        top = fixed_top
    if handle in ("nw", "ne"):
        bottom = fixed_bottom

    x_edges = [0, bounds_width]
    y_edges = [0, bounds_height]
    for other in others:
        x_edges.extend([other.left, other.right])
        y_edges.extend([other.top, other.bottom])

    if handle in ("nw", "sw"):
        left = _nearest(left, x_edges, threshold)
    if handle in ("ne", "se"):
        right = _nearest(right, x_edges, threshold)
    if handle in ("nw", "ne"):
        top = _nearest(top, y_edges, threshold)
    if handle in ("sw", "se"):
        bottom = _nearest(bottom, y_edges, threshold)

    if lock_aspect and start.height > 0:
        ratio = start.width / float(start.height)
        left, top, right, bottom = _apply_aspect_ratio(left, top, right, bottom, handle, ratio)

    # Clamp moved edges against viewport and minimum sizes.
    if handle in ("nw", "sw"):
        left = min(max(0, left), fixed_right - min_width)
    if handle in ("ne", "se"):
        right = max(fixed_left + min_width, min(bounds_width, right))
    if handle in ("nw", "ne"):
        top = min(max(0, top), fixed_bottom - min_height)
    if handle in ("sw", "se"):
        bottom = max(fixed_top + min_height, min(bounds_height, bottom))

    width = max(min_width, right - left)
    height = max(min_height, bottom - top)

    # Ensure final rect remains fully visible.
    width = min(width, bounds_width)
    height = min(height, bounds_height)
    max_x = max(0, bounds_width - width)
    max_y = max(0, bounds_height - height)
    x = min(max(0, left), max_x)
    y = min(max(0, top), max_y)

    return Rect(x=x, y=y, width=width, height=height)
