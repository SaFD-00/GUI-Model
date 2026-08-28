"""Canonical roots for Atlas-Collector data and runtime artifacts.

Two roots, deliberately separate:

    data/raw/{package}/       durable — the collected triples (xml + png + action)
    data/export/              durable — the Stage-1 jsonl + images produced by M5
    runtime/apps/{package}/   ephemeral — per-app session bookkeeping
    runtime/logs/             ephemeral — per-run loguru sinks

Everything that resolves a per-app path goes through :func:`apps_root` /
:func:`raw_app_dir`, so a consumer can never write under ``apps/`` while another
reads the bare runtime root. The sub-roots are siblings: iterating
``apps_root(runtime_dir)`` yields package dirs only, with no ``logs`` entry to skip.

``project_root()`` is the anchor for the repo-relative defaults in
``config/run.yaml``. It is derived from this file's location (``src`` layout:
``src/atlas_collector/paths.py`` -> ``parents[2]``), which stays correct under an
editable ``uv sync`` install.
"""

from __future__ import annotations

from pathlib import Path

RAW_SUBDIR = "raw"
EXPORT_SUBDIR = "export"
APPS_SUBDIR = "apps"
LOGS_SUBDIR = "logs"


def project_root() -> Path:
    """Atlas-Collector repo root (the directory holding ``pyproject.toml``)."""
    return Path(__file__).resolve().parents[2]


def config_path(name: str = "run.yaml") -> Path:
    """Path to a file under ``config/``. Existence is NOT checked here."""
    return project_root() / "config" / name


def catalog_path(name: str = "apps.csv") -> Path:
    """Path to a file under ``catalog/``. Existence is NOT checked here."""
    return project_root() / "catalog" / name


def _resolve(base: str | Path) -> Path:
    """Interpret *base* relative to the project root when it is not absolute."""
    path = Path(base)
    return path if path.is_absolute() else project_root() / path


def raw_root(data_dir: str | Path = "data") -> Path:
    """Durable collection root: ``{data_dir}/raw``."""
    return _resolve(data_dir) / RAW_SUBDIR


def raw_app_dir(data_dir: str | Path, package: str) -> Path:
    """Durable per-package collection dir: ``{data_dir}/raw/{package}``."""
    return raw_root(data_dir) / package


def export_root(data_dir: str | Path = "data") -> Path:
    """Stage-1 export root: ``{data_dir}/export``."""
    return _resolve(data_dir) / EXPORT_SUBDIR


def apps_root(runtime_dir: str | Path = "runtime") -> Path:
    """Root holding one directory per collected package: ``{runtime_dir}/apps``."""
    return _resolve(runtime_dir) / APPS_SUBDIR


def app_dir(runtime_dir: str | Path, package: str) -> Path:
    """Ephemeral runtime dir for a single package: ``{runtime_dir}/apps/{package}``."""
    return apps_root(runtime_dir) / package


def logs_root(runtime_dir: str | Path = "runtime") -> Path:
    """Root holding per-run log files: ``{runtime_dir}/logs``."""
    return _resolve(runtime_dir) / LOGS_SUBDIR


__all__ = [
    "APPS_SUBDIR",
    "EXPORT_SUBDIR",
    "LOGS_SUBDIR",
    "RAW_SUBDIR",
    "app_dir",
    "apps_root",
    "catalog_path",
    "config_path",
    "export_root",
    "logs_root",
    "project_root",
    "raw_app_dir",
    "raw_root",
]
