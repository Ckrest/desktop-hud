"""YAML config loader with hot-reload support."""

import logging
import os
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG_PATH = PACKAGE_DIR / "config.example.yaml"
CONFIG_PATH = PACKAGE_DIR / "config.local.yaml"


def _expand_paths(obj):
    """Recursively expand ~ in string values that look like paths."""
    if isinstance(obj, str):
        if obj.startswith("~/") or obj.startswith("~\\"):
            return str(Path(obj).expanduser())
        return obj
    if isinstance(obj, dict):
        return {k: _expand_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_paths(v) for v in obj]
    return obj


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base recursively."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    """Load config from example + local override."""
    config = {}

    if EXAMPLE_CONFIG_PATH.exists():
        with open(EXAMPLE_CONFIG_PATH) as f:
            data = yaml.safe_load(f)
            if data:
                config = data

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = yaml.safe_load(f)
            if data:
                config = _deep_merge(config, data)
        log.info("Loaded config override from %s", CONFIG_PATH)

    config = _expand_paths(config)
    return config
