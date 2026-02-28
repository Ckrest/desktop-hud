"""Layout profile management for desktop-hud."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class LayoutProfileError(Exception):
    """Raised when a profile operation cannot be completed."""


class LayoutProfileManager:
    """Load/save named layout profiles and autosave the last-used layout."""

    def __init__(self, package_dir: Path, config: dict):
        layouts_cfg = config.get("layouts", {})

        self.directory = self._resolve_directory(
            package_dir,
            layouts_cfg.get("directory", "layouts"),
        )
        self.default_profile = str(layouts_cfg.get("default_profile", "default"))
        self.last_used_profile = str(layouts_cfg.get("last_used_profile", "last-used"))
        self.autosave_last_used = bool(layouts_cfg.get("autosave_last_used", True))
        self.startup_profiles: list[str] = layouts_cfg.get(
            "startup_profiles", [self.default_profile],
        )

        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_directory(package_dir: Path, path_value: str) -> Path:
        raw = Path(str(path_value)).expanduser()
        if raw.is_absolute():
            return raw
        return (package_dir / raw).resolve()

    @staticmethod
    def _validate_profile_name(name: str) -> str:
        normalized = str(name).strip()
        if not normalized:
            raise LayoutProfileError("Profile name is required")
        if not PROFILE_NAME_RE.match(normalized):
            raise LayoutProfileError(
                "Profile names may only include letters, numbers, dot, underscore, and dash",
            )
        return normalized

    def _profile_path(self, name: str) -> Path:
        valid_name = self._validate_profile_name(name)
        return self.directory / f"{valid_name}.yaml"

    def list_profiles(self) -> list[str]:
        names: list[str] = []
        for path in sorted(self.directory.glob("*.yaml")):
            stem = path.stem
            if PROFILE_NAME_RE.match(stem):
                names.append(stem)
        return names

    def load_profile(self, name: str) -> list[dict]:
        """Load a profile and return full element definitions."""
        path = self._profile_path(name)
        if not path.exists():
            raise FileNotFoundError(path)

        with open(path) as handle:
            data = yaml.safe_load(handle) or {}

        return data.get("elements", [])

    def load_profiles(self, names: list[str]) -> list[dict]:
        """Load multiple profiles additively. Later profiles override by element id."""
        elements_by_id: dict[str, dict] = {}
        for name in names:
            for elem in self.load_profile(name):
                elem_id = elem.get("id")
                if elem_id:
                    elements_by_id[elem_id] = elem
        return list(elements_by_id.values())

    def save_profile(self, name: str, elements: list[dict]) -> Path:
        path = self._profile_path(name)

        serializable = []
        for item in elements:
            if not item.get("editable", True):
                continue
            entry = dict(item)
            # Remove runtime-only keys
            entry.pop("source", None)
            entry.pop("editable", None)
            entry.pop("__source", None)
            serializable.append(entry)

        payload = {
            "name": self._validate_profile_name(name),
            "meta": {
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "elements": serializable,
        }

        self._atomic_write_yaml(path, payload)
        return path

    def ensure_profile_exists(self, name: str, elements: list[dict]) -> None:
        path = self._profile_path(name)
        if path.exists():
            return
        self.save_profile(name, elements)
        log.info("Created missing profile '%s' at %s", name, path)

    def save_last_used(self, elements: list[dict]) -> Path | None:
        if not self.autosave_last_used:
            return None
        return self.save_profile(self.last_used_profile, elements)

    @staticmethod
    def _atomic_write_yaml(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(tmp_name)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(data, handle, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
