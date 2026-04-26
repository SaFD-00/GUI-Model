"""Static activity ground-truth source from ``catalog/activities.json``.

Produced by ``catalog/extract_activities.py`` (androguard parsing of each
APK's ``AndroidManifest.xml``) and loaded once per process. Used by the
activity coverage tracker so that the denominator (``total_activities``)
is fixed across sessions and devices, instead of varying with ``dumpsys``
output.

Falls back silently to ``None`` when the catalog file is missing, corrupt,
or the package is not registered — callers are expected to handle the
fallback path (typically ``adb.get_declared_activities``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from loguru import logger

# <repo>/src/monkey_collector/catalog_activities.py
#   parents[2] = <repo root>
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "catalog" / "activities.json"


class ActivityCatalog:
    """Process-lifetime cache of ``catalog/activities.json``."""

    _instance: ClassVar[ActivityCatalog | None] = None

    def __init__(self, path: Path | None = None) -> None:
        self._data: dict[str, list[str]] = {}
        self._loaded: bool = False
        self._try_load(path or _DEFAULT_PATH)

    @classmethod
    def instance(cls, path: Path | None = None) -> ActivityCatalog:
        if cls._instance is None:
            cls._instance = cls(path)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Clear cached singleton — intended for tests only."""
        cls._instance = None

    def _try_load(self, path: Path) -> None:
        if not path.exists():
            logger.error(
                f"Activity catalog not found at {path}; "
                f"falling back to dumpsys for all packages"
            )
            return
        try:
            with path.open(encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("expected JSON object at top level")
            self._data = {
                pkg: list(entry.get("activities") or [])
                for pkg, entry in raw.items()
                if isinstance(entry, dict)
            }
            self._loaded = True
            logger.info(
                f"Activity catalog loaded: {len(self._data)} packages from {path}"
            )
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.error(
                f"Activity catalog at {path} unreadable ({e}); "
                f"falling back to dumpsys for all packages"
            )

    def is_loaded(self) -> bool:
        return self._loaded

    def get_declared(self, package: str) -> list[str] | None:
        """Return a fresh list of declared activities, or ``None`` on miss."""
        if not self._loaded:
            return None
        acts = self._data.get(package)
        if acts is None:
            return None
        return list(acts)
