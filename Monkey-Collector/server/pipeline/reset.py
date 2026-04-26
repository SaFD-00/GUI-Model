"""Reset: delete collected session data by scope (all / apps)."""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger


def resolve_targets(
    output_dir: str | Path,
    all_: bool = False,
    packages: list[str] | None = None,
) -> list[Path]:
    """Return existing directories that match the reset scope.

    * ``all_=True``   → ``[output_dir]`` (if it exists).
    * ``packages``    → ``[output_dir / pkg for each existing pkg dir]``.

    Raises ValueError if no scope is given.
    """
    output_dir = Path(output_dir)

    if all_:
        return [output_dir] if output_dir.exists() else []

    if packages:
        return [
            p for p in (output_dir / pkg for pkg in packages)
            if p.exists()
        ]

    raise ValueError("reset requires a scope: --all or --apps")


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
