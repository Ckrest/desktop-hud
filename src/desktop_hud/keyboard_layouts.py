"""Keyboard layout asset loading for generic HUD keyboard elements."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from desktop_hud.config import PACKAGE_DIR


KEYBOARD_ASSET_DIR = PACKAGE_DIR / "assets" / "keyboards"


class KeyboardLayoutError(ValueError):
    """Raised when a keyboard layout asset cannot be loaded or validated."""


def _asset_path(name: str) -> Path:
    normalized = str(name).strip()
    if not normalized:
        raise KeyboardLayoutError("Keyboard layout asset name is required")
    if normalized.endswith(".yaml"):
        filename = normalized
    else:
        filename = f"{normalized}.yaml"
    path = (KEYBOARD_ASSET_DIR / filename).resolve()
    if KEYBOARD_ASSET_DIR.resolve() not in path.parents and path != KEYBOARD_ASSET_DIR.resolve():
        raise KeyboardLayoutError(f"Keyboard layout asset escapes asset directory: {name}")
    return path


@lru_cache(maxsize=32)
def load_keyboard_layout_asset(name: str) -> dict[str, Any]:
    """Load a keyboard layout asset by name from assets/keyboards."""

    path = _asset_path(name)
    if not path.exists():
        raise KeyboardLayoutError(f"Keyboard layout asset does not exist: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise KeyboardLayoutError(f"Keyboard layout asset must be a mapping: {path}")
    return validate_keyboard_layout(data, source=str(path))


def resolve_keyboard_layout(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve inline or asset-backed keyboard layout data from an element config."""

    layout = config.get("layout")
    if isinstance(layout, dict):
        if layout.get("asset"):
            return load_keyboard_layout_asset(str(layout["asset"]))
        return validate_keyboard_layout(layout, source=f"element:{config.get('id', '')}")
    if isinstance(layout, str) and layout.strip():
        return load_keyboard_layout_asset(layout)
    asset = config.get("layout_asset") or config.get("asset") or "us-ansi"
    return load_keyboard_layout_asset(str(asset))


def validate_keyboard_layout(data: dict[str, Any], source: str = "inline") -> dict[str, Any]:
    keys = data.get("keys")
    if not isinstance(keys, list) or not keys:
        raise KeyboardLayoutError(f"Keyboard layout has no keys: {source}")

    validated = dict(data)
    normalized_keys = []
    for index, raw_key in enumerate(keys):
        if not isinstance(raw_key, dict):
            raise KeyboardLayoutError(f"Keyboard layout key #{index} must be a mapping: {source}")
        code = str(raw_key.get("code", "")).strip()
        label = str(raw_key.get("label", "")).strip()
        if not code:
            raise KeyboardLayoutError(f"Keyboard layout key #{index} is missing code: {source}")
        try:
            x = float(raw_key.get("x", 0))
            y = float(raw_key.get("y", 0))
            w = float(raw_key.get("w", 1))
            h = float(raw_key.get("h", 1))
        except (TypeError, ValueError) as exc:
            raise KeyboardLayoutError(f"Keyboard layout key '{code}' has invalid geometry: {source}") from exc
        normalized_key = dict(raw_key)
        normalized_key.update({"code": code, "label": label or code.removeprefix("KEY_"), "x": x, "y": y, "w": w, "h": h})
        normalized_keys.append(normalized_key)

    validated["keys"] = normalized_keys
    return validated
