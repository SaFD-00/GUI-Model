"""Canonical roots for Atlas-Collector data and runtime artifacts.

ONE COLLECTION ROOT
===================

Everything a run produces — intermediate and final — lives under a single root,
by default ``data/AtlasCollection`` (named to sit beside ``data/MonkeyCollection``,
where the training pipeline reads the sibling collector's corpus from)::

    data/AtlasCollection/
        raw/{package}/          durable — collected triples (xml + png + action)
        runtime/apps/{package}/ ephemeral — per-app session bookkeeping
        runtime/logs/           ephemeral — per-run loguru sinks
        stage1_train.jsonl      durable — the Stage-1 export
        stage1_test_id.jsonl
        stage1_test_ood.jsonl
        images/

One root because a run's intermediate and final artifacts are one dataset: split
across ``data/raw``, ``runtime`` and ``data/AtlasCollection`` they can be deleted,
copied or archived out of step, and a stale ``raw/`` beside a fresh export is
indistinguishable from a consistent one. It also makes "throw this pilot away"
a single removal instead of three that must all be remembered.

``raw`` and ``runtime`` stay distinct SUBTREES within it: their lifetimes still
differ (the corpus is the product, the runtime state is scaffolding), and
:func:`apps_root` / :func:`raw_app_dir` remain the only way to resolve a per-app
path so a consumer can never write under ``apps/`` while another reads the bare
runtime root.

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

#: The single collection root, relative to the project unless absolute.
DEFAULT_ROOT = "data/AtlasCollection"

RAW_SUBDIR = "raw"
RUNTIME_SUBDIR = "runtime"
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


def collection_root(root: str | Path = DEFAULT_ROOT) -> Path:
    """The single root holding everything one collection produces."""
    return _resolve(root)


def raw_root(root: str | Path = DEFAULT_ROOT) -> Path:
    """Durable collection subtree: ``{root}/raw``."""
    return collection_root(root) / RAW_SUBDIR


def raw_app_dir(root: str | Path, package: str) -> Path:
    """Durable per-package collection dir: ``{root}/raw/{package}``."""
    return raw_root(root) / package


def runtime_root(root: str | Path = DEFAULT_ROOT) -> Path:
    """Ephemeral run-state subtree: ``{root}/runtime``."""
    return collection_root(root) / RUNTIME_SUBDIR


def export_root(root: str | Path = DEFAULT_ROOT) -> Path:
    """Stage-1 export lands at the root itself, beside ``raw`` and ``runtime``."""
    return collection_root(root)


def apps_root(runtime_dir: str | Path) -> Path:
    """Root holding one directory per collected package: ``{runtime_dir}/apps``."""
    return _resolve(runtime_dir) / APPS_SUBDIR


def app_dir(runtime_dir: str | Path, package: str) -> Path:
    """Ephemeral runtime dir for a single package: ``{runtime_dir}/apps/{package}``."""
    return apps_root(runtime_dir) / package


def logs_root(runtime_dir: str | Path) -> Path:
    """Root holding per-run log files: ``{runtime_dir}/logs``."""
    return _resolve(runtime_dir) / LOGS_SUBDIR


__all__ = [
    "APPS_SUBDIR",
    "DEFAULT_ROOT",
    "LOGS_SUBDIR",
    "RAW_SUBDIR",
    "RUNTIME_SUBDIR",
    "app_dir",
    "collection_root",
    "apps_root",
    "catalog_path",
    "config_path",
    "export_root",
    "logs_root",
    "project_root",
    "raw_app_dir",
    "raw_root",
    "runtime_root",
]
