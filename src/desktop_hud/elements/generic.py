"""Generic GTK-native HUD elements."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from desktop_hud.elements.base import HudElement
from desktop_hud.keyboard_layouts import resolve_keyboard_layout


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _add_class(widget: Gtk.Widget, *names: str) -> Gtk.Widget:
    for name in names:
        if name:
            widget.add_css_class(name)
    return widget


def _label(text: Any, css_class: str = "", xalign: float = 0.0, wrap: bool = True) -> Gtk.Label:
    label = Gtk.Label(label=_text(text))
    if css_class:
        label.add_css_class(css_class)
    label.set_xalign(xalign)
    label.set_wrap(wrap)
    label.set_selectable(False)
    return label


class TextElement(HudElement):
    """Simple title/body/status text block."""

    RECREATE_FIELDS = {"title", "body", "status", "align", "classes"}

    def runtime_update_requires_recreate(self, updates: dict) -> bool:
        return any(key in self.RECREATE_FIELDS for key in updates)

    def create_widget(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        _add_class(box, "hud-text", *self.config.get("classes", []))
        box.set_valign(Gtk.Align.FILL)
        box.set_halign(Gtk.Align.FILL)

        title = self.config.get("title")
        body = self.config.get("body")
        status = self.config.get("status")
        if title:
            box.append(_label(title, "hud-title"))
        if body:
            box.append(_label(body, "hud-body"))
        if status:
            box.append(_label(status, "hud-status"))
        if not any((title, body, status)):
            box.append(_label("", "hud-body"))
        self.widget = box
        return box


class ListElement(HudElement):
    """Structured list rows with key, label, detail, state, icon, and action IDs."""

    RECREATE_FIELDS = {"rows", "title", "density", "classes"}

    def runtime_update_requires_recreate(self, updates: dict) -> bool:
        return any(key in self.RECREATE_FIELDS for key in updates)

    def create_widget(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        _add_class(outer, "hud-list", f"hud-density-{self.config.get('density', 'normal')}")
        if self.config.get("title"):
            outer.append(_label(self.config["title"], "hud-subtitle"))

        rows = self.config.get("rows") or self.config.get("items") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            outer.append(self._build_row(row))
        self.widget = outer
        return outer

    def _build_row(self, row: dict[str, Any]) -> Gtk.Widget:
        button = Gtk.Button()
        _add_class(button, "hud-list-row")
        state = _text(row.get("state"), "default").strip().lower()
        if state and state != "default":
            button.add_css_class(f"hud-list-row-{state}")
        action_id = row.get("action_id") or row.get("action")
        if action_id:
            button.set_name(str(action_id))
        button._hud_row_payload = dict(row)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        content.set_halign(Gtk.Align.FILL)
        content.set_hexpand(True)

        key = row.get("key")
        if key:
            key_label = _label(key, "hud-list-key", xalign=0.5, wrap=False)
            key_label.set_width_chars(5)
            content.append(key_label)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.set_hexpand(True)
        labels.append(_label(row.get("label", row.get("title", "")), "hud-list-label"))
        if row.get("detail"):
            labels.append(_label(row["detail"], "hud-list-detail"))
        content.append(labels)

        if row.get("state"):
            content.append(_label(row["state"], "hud-list-state", xalign=1.0, wrap=False))

        button.set_child(content)
        return button


class PanelElement(HudElement):
    """Visual container with title, subtitle, sections, rows, and footer."""

    RECREATE_FIELDS = {"title", "subtitle", "sections", "rows", "footer", "density", "classes"}

    def runtime_update_requires_recreate(self, updates: dict) -> bool:
        return any(key in self.RECREATE_FIELDS for key in updates)

    def create_widget(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        _add_class(panel, "hud-panel", f"hud-density-{self.config.get('density', 'normal')}")

        if self.config.get("title"):
            panel.append(_label(self.config["title"], "hud-title"))
        if self.config.get("subtitle"):
            panel.append(_label(self.config["subtitle"], "hud-subtitle"))

        rows = self.config.get("rows")
        if rows:
            panel.append(ListElement({**self.config, "id": f"{self.id}:rows", "type": "list", "rows": rows}).create_widget())

        for section in self.config.get("sections") or []:
            if not isinstance(section, dict):
                continue
            section_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            section_box.add_css_class("hud-panel-section")
            if section.get("title"):
                section_box.append(_label(section["title"], "hud-section-title"))
            if section.get("body"):
                section_box.append(_label(section["body"], "hud-body"))
            if section.get("rows"):
                section_box.append(ListElement({"id": f"{self.id}:section", "type": "list", "rows": section["rows"]}).create_widget())
            panel.append(section_box)

        if self.config.get("footer"):
            panel.append(_label(self.config["footer"], "hud-footer"))
        self.widget = panel
        return panel


class ToastElement(HudElement):
    """Compact transient notification."""

    RECREATE_FIELDS = {"title", "summary", "detail", "intent", "classes"}

    def runtime_update_requires_recreate(self, updates: dict) -> bool:
        return any(key in self.RECREATE_FIELDS for key in updates)

    def create_widget(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        intent = _text(self.config.get("intent"), "info").strip().lower()
        _add_class(box, "hud-toast", f"hud-toast-{intent}")
        box.append(_label(self.config.get("title", self.config.get("summary", "")), "hud-toast-title"))
        summary = self.config.get("summary")
        if summary and summary != self.config.get("title"):
            box.append(_label(summary, "hud-toast-summary"))
        if self.config.get("detail"):
            box.append(_label(self.config["detail"], "hud-toast-detail"))
        self.widget = box
        return box


class TableElement(HudElement):
    """Dense structured diagnostic table."""

    RECREATE_FIELDS = {"columns", "rows", "title", "classes"}

    def runtime_update_requires_recreate(self, updates: dict) -> bool:
        return any(key in self.RECREATE_FIELDS for key in updates)

    def create_widget(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.add_css_class("hud-table")
        if self.config.get("title"):
            outer.append(_label(self.config["title"], "hud-subtitle"))

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(4)
        columns = self.config.get("columns") or []
        rows = self.config.get("rows") or []
        for col, column in enumerate(columns):
            heading = column.get("label", column.get("key", "")) if isinstance(column, dict) else column
            cell = _label(heading, "hud-table-heading", wrap=False)
            grid.attach(cell, col, 0, 1, 1)
        for row_index, row in enumerate(rows, start=1):
            for col, column in enumerate(columns):
                key = column.get("key") if isinstance(column, dict) else str(column)
                value = row.get(key, "") if isinstance(row, dict) else ""
                grid.attach(_label(value, "hud-table-cell", wrap=False), col, row_index, 1, 1)
        outer.append(grid)
        self.widget = outer
        return outer


class KeyboardElement(HudElement):
    """Generic keyboard layout renderer with caller-provided key states."""

    RECREATE_FIELDS = {"layout", "layout_asset", "asset", "keys", "reserved", "legend", "profile", "layer"}
    VALID_STATES = {"default", "active", "reserved", "conflict", "warning", "unavailable", "pressed", "disabled"}

    def runtime_update_requires_recreate(self, updates: dict) -> bool:
        return any(key in self.RECREATE_FIELDS for key in updates)

    def create_widget(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.add_css_class("hud-keyboard")
        try:
            root.set_overflow(Gtk.Overflow.HIDDEN)
        except Exception:
            pass

        title_parts = [part for part in (self.config.get("profile"), self.config.get("layer")) if part]
        if title_parts:
            root.append(_label(" / ".join(str(part) for part in title_parts), "hud-subtitle"))

        layout = resolve_keyboard_layout(self.config)
        fixed = Gtk.Fixed()
        fixed.set_hexpand(True)
        fixed.set_vexpand(True)
        fixed.add_css_class("hud-keyboard-layout")
        try:
            fixed.set_overflow(Gtk.Overflow.HIDDEN)
        except Exception:
            pass

        key_states = self._key_state_map()
        gap = float(self.config.get("gap_px", 4))
        unit = self._unit_size(layout, gap, bool(title_parts), bool(self.config.get("legend")))
        bounds_width, bounds_height = self._layout_bounds(layout, unit, gap)
        fixed.set_size_request(bounds_width, bounds_height)

        for key in layout["keys"]:
            x = int(round(key["x"] * (unit + gap)))
            y = int(round(key["y"] * (unit + gap)))
            width = max(1, int(round(key["w"] * unit + max(0, key["w"] - 1) * gap)))
            height = max(1, int(round(key["h"] * unit + max(0, key["h"] - 1) * gap)))
            widget = self._build_key(key, key_states.get(key["code"], {}), width, height)
            widget.set_size_request(width, height)
            fixed.put(widget, x, y)

        root.append(fixed)
        legend = self.config.get("legend") or []
        if legend:
            legend_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            legend_box.add_css_class("hud-keyboard-legend")
            for item in legend:
                legend_box.append(self._build_legend_item(item))
            root.append(legend_box)
        self.widget = root
        return root

    def _build_legend_item(self, item: Any) -> Gtk.Widget:
        state = "default"
        text = item
        if isinstance(item, dict):
            state = _text(item.get("state"), "default").strip().lower()
            text = item.get("label", state)
        if state not in self.VALID_STATES:
            state = "default"

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.add_css_class("hud-key")
        box.add_css_class("hud-keyboard-legend-item")
        if state != "default":
            box.add_css_class(f"hud-key-{state}")
        label = _label(text, "hud-key-label", xalign=0.5, wrap=False)
        box.append(label)
        return box

    def _available_size(self, has_title: bool, has_legend: bool) -> tuple[int, int]:
        resolved = self.config.get("resolved_frame") if isinstance(self.config.get("resolved_frame"), dict) else {}
        width = int(resolved.get("width") or self.config.get("size", {}).get("width") or self.size[0])
        height = int(resolved.get("height") or self.config.get("size", {}).get("height") or self.size[1])
        padding = int(self.config.get("padding_px", 28))
        title_height = 24 if has_title else 0
        legend_height = 30 if has_legend else 0
        return max(1, width - padding), max(1, height - padding - title_height - legend_height)

    @staticmethod
    def _layout_units(layout: dict[str, Any]) -> tuple[float, float]:
        max_right = 1.0
        max_bottom = 1.0
        for key in layout.get("keys") or []:
            max_right = max(max_right, float(key.get("x", 0)) + float(key.get("w", 1)))
            max_bottom = max(max_bottom, float(key.get("y", 0)) + float(key.get("h", 1)))
        return max_right, max_bottom

    def _unit_size(self, layout: dict[str, Any], gap: float, has_title: bool, has_legend: bool) -> float:
        configured = self.config.get("unit_px")
        if configured is not None:
            return max(8.0, float(configured))

        available_width, available_height = self._available_size(has_title, has_legend)
        units_width, units_height = self._layout_units(layout)
        width_gap = max(0.0, (units_width - 1.0) * gap)
        height_gap = max(0.0, (units_height - 1.0) * gap)
        unit_by_width = (available_width - width_gap) / units_width
        unit_by_height = (available_height - height_gap) / units_height
        return max(8.0, min(unit_by_width, unit_by_height))

    def _layout_bounds(self, layout: dict[str, Any], unit: float, gap: float) -> tuple[int, int]:
        units_width, units_height = self._layout_units(layout)
        width = int(round(units_width * unit + max(0.0, units_width - 1.0) * gap))
        height = int(round(units_height * unit + max(0.0, units_height - 1.0) * gap))
        return max(1, width), max(1, height)

    def _key_state_map(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in self.config.get("reserved") or []:
            if not isinstance(item, dict):
                continue
            key = item.get("key") or item.get("code") or self._binding_key(item.get("binding", ""))
            if key:
                result[str(key)] = {**item, "state": "reserved"}
        for item in self.config.get("keys") or []:
            if not isinstance(item, dict):
                continue
            key = item.get("key") or item.get("code") or self._binding_key(item.get("binding", ""))
            if key:
                result[str(key)] = item
        return result

    def _build_key(self, layout_key: dict[str, Any], state_data: dict[str, Any], width: int, height: int) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("hud-key")
        box.set_size_request(width, height)
        box.set_hexpand(False)
        box.set_vexpand(False)
        box.set_halign(Gtk.Align.START)
        box.set_valign(Gtk.Align.START)
        try:
            box.set_overflow(Gtk.Overflow.HIDDEN)
        except Exception:
            pass
        state = _text(state_data.get("state"), "default").strip().lower()
        if state not in self.VALID_STATES:
            state = "default"
        if state != "default":
            box.add_css_class(f"hud-key-{state}")
        box.set_tooltip_text(_text(state_data.get("label") or state_data.get("detail") or layout_key.get("label")))

        inner_width = max(1, width - 12)
        label = _label(state_data.get("key_label") or layout_key.get("label"), "hud-key-label", xalign=0.5, wrap=False)
        label.set_size_request(inner_width, -1)
        label.set_max_width_chars(max(2, int(inner_width / 8)))
        label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(label)

        label = state_data.get("label")
        if label:
            action = _label(label, "hud-key-action", xalign=0.5, wrap=True)
            action.set_size_request(inner_width, -1)
            action.set_max_width_chars(max(3, int(inner_width / 7)))
            action.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            action.set_lines(max(1, int((height - 28) / 15)))
            action.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(action)
        return box

    @staticmethod
    def _binding_key(binding: str) -> str:
        parts = str(binding).strip().split()
        return parts[-1] if parts else ""
