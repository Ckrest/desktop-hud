"""Trait-based element discovery from other packages."""

import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


def discover_trait_elements() -> list[dict]:
    """Find packages with the desktop-hud trait and collect their elements."""
    elements = []

    try:
        from systems import get_package_path
        from systems.registry import get_packages_with_trait
    except ImportError:
        log.debug("systems package not available, skipping trait discovery")
        return elements

    try:
        packages = get_packages_with_trait("desktop-hud")
    except Exception:
        log.exception("Failed to query packages with desktop-hud trait")
        return elements

    for pkg_name in packages:
        try:
            pkg_path = get_package_path(pkg_name)
            if pkg_path is None:
                log.warning("Could not resolve path for package '%s'", pkg_name)
                continue

            trait_file = Path(pkg_path) / "trait_desktop-hud.yaml"
            if not trait_file.exists():
                log.warning("Trait file missing for '%s': %s", pkg_name, trait_file)
                continue

            with open(trait_file) as f:
                data = yaml.safe_load(f)

            if not data or "elements" not in data:
                continue

            pkg_path_str = str(pkg_path)
            for elem in data["elements"]:
                # Resolve {package_path} placeholders
                _resolve_placeholders(elem, pkg_path_str)
                # Prefix element IDs with package name to avoid collisions
                if "id" in elem:
                    elem["id"] = f"{pkg_name}:{elem['id']}"
                elements.append(elem)

            log.info("Discovered %d elements from '%s'", len(data["elements"]), pkg_name)

        except Exception:
            log.exception("Failed to load trait elements from '%s'", pkg_name)

    return elements


def _resolve_placeholders(obj, package_path: str):
    """Recursively replace {package_path} in string values."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                obj[key] = value.replace("{package_path}", package_path)
            elif isinstance(value, (dict, list)):
                _resolve_placeholders(value, package_path)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i] = item.replace("{package_path}", package_path)
            elif isinstance(item, (dict, list)):
                _resolve_placeholders(item, package_path)
