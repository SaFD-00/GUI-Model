"""Reset: delete collected session data by scope (all / categories / packages)."""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from server.pipeline.app_catalog import AppCatalog


def resolve_targets(
    output_dir: str | Path,
    all_: bool = False,
    categories: list[str] | None = None,
    packages: list[str] | None = None,
    apps_csv: str | Path | None = None,
    priorities: list[str] | None = None,
) -> list[Path]:
    """Return existing directories that match the reset scope.

    Precedence:
      1. all_=True              → [output_dir] if it exists
      2. apps_csv               → filter catalog, return {output}/{cat}/{pkg}
      3. packages               → {output}/*/{pkg} (intersected with categories if given)
      4. categories alone       → {output}/{cat}

    Raises ValueError if no scope is given.
    """
    output_dir = Path(output_dir)

    if all_:
        return [output_dir] if output_dir.exists() else []

    if apps_csv is not None:
        catalog = AppCatalog.load(apps_csv)
        jobs = catalog.filter(categories=categories, priorities=priorities)
        return [
            p for p in (
                output_dir / j.category / j.package_id for j in jobs
            )
            if p.exists()
        ]

    if packages:
        cat_scope: list[Path]
        if categories:
            cat_scope = [output_dir / c for c in categories]
        else:
            cat_scope = [d for d in output_dir.iterdir() if d.is_dir()] \
                if output_dir.exists() else []
        return [
            p for p in (c / pkg for c in cat_scope for pkg in packages)
            if p.exists()
        ]

    if categories:
        return [p for p in (output_dir / c for c in categories) if p.exists()]

    raise ValueError(
        "reset requires a scope: --all, --categories, --packages, or --apps-csv"
    )


def delete_targets(targets: list[Path], dry_run: bool = False) -> int:
    """Delete directories via shutil.rmtree. Return number deleted."""
    deleted = 0
    for path in targets:
        if not path.exists():
            continue
        if dry_run:
            logger.info(f"[dry-run] would delete: {path}")
            continue
        shutil.rmtree(path)
        logger.info(f"Deleted: {path}")
        deleted += 1
    return deleted


__all__ = ["delete_targets", "resolve_targets"]
