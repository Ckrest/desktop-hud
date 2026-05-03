"""Generic GTK-native HUD elements."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

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

        title_parts = [part for part in (self.config.get("profile"), self.config.get("layer")) if part]
        if title_parts:
            root.append(_label(" / ".join(str(part) for part in title_parts), "hud-subtitle"))

        layout = resolve_keyboard_layout(self.config)
        fixed = Gtk.Fixed()
        fixed.set_hexpand(True)
        fixed.set_vexpand(True)
        fixed.add_css_class("hud-keyboard-layout")

        key_states = self._key_state_map()
        unit = float(self.config.get("unit_px", 42))
        gap = float(self.config.get("gap_px", 4))
        for key in layout["keys"]:
            widget = self._build_key(key, key_states.get(key["code"], {}))
            x = int(round(key["x"] * (unit + gap)))
            y = int(round(key["y"] * (unit + gap)))
            width = max(1, int(round(key["w"] * unit + max(0, key["w"] - 1) * gap)))
            height = max(1, int(round(key["h"] * unit + max(0, key["h"] - 1) * gap)))
            widget.set_size_request(width, height)
            fixed.put(widget, x, y)

        root.append(fixed)
        legend = self.config.get("legend") or []
        if legend:
            legend_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            legend_box.add_css_class("hud-keyboard-legend")
            for item in legend:
                if isinstance(item, dict):
                    legend_box.append(_label(item.get("label", item.get("state", "")), f"hud-key-{item.get('state', 'default')}", wrap=False))
                else:
                    legend_box.append(_label(item, "hud-keyboard-legend-item", wrap=False))
            root.append(legend_box)
        self.widget = root
        return root

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

    def _build_key(self, layout_key: dict[str, Any], state_data: dict[str, Any]) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("hud-key")
        state = _text(state_data.get("state"), "default").strip().lower()
        if state not in self.VALID_STATES:
            state = "default"
        if state != "default":
            box.add_css_class(f"hud-key-{state}")
        box.set_tooltip_text(_text(state_data.get("label") or state_data.get("detail") or layout_key.get("label")))

        box.append(_label(state_data.get("key_label") or layout_key.get("label"), "hud-key-label", xalign=0.5, wrap=False))
        label = state_data.get("label")
        if label:
            box.append(_label(label, "hud-key-action", xalign=0.5, wrap=True))
        return box

    @staticmethod
    def _binding_key(binding: str) -> str:
        parts = str(binding).strip().split()
        return parts[-1] if parts else ""
