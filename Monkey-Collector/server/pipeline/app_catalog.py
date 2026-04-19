"""App catalog: parse apps.csv and filter by category/priority."""

from __future__ import annotations

import csv
from dataclasses import dataclass, fields
from pathlib import Path

from loguru import logger

_REQUIRED_COLUMNS = (
    "category",
    "sub_category",
    "app_name",
    "package_id",
    "source",
    "priority",
    "notes",
)


@dataclass(frozen=True)
class AppJob:
    category: str
    sub_category: str
    app_name: str
    package_id: str
    source: str
    priority: str
    notes: str = ""


def _normalize(value: str) -> str:
    return value.strip().lower()


class AppCatalog:
    def __init__(self, apps: list[AppJob]) -> None:
        self._apps: list[AppJob] = list(apps)

    @classmethod
    def load(cls, csv_path: str | Path) -> AppCatalog:
        """Parse apps.csv using the stdlib csv module (no pandas)."""
        path = Path(csv_path)
        apps: list[AppJob] = []
        # utf-8-sig strips a leading BOM if present.
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise ValueError(f"Empty CSV: {path}") from exc

            columns = [_normalize(h) for h in header]
            missing = [c for c in _REQUIRED_COLUMNS if c not in columns]
            if missing:
                raise ValueError(
                    f"apps.csv missing required columns: {missing} (got {columns})"
                )
            index = {name: columns.index(name) for name in _REQUIRED_COLUMNS}

            for line_no, row in enumerate(reader, start=2):
                if not row or all(not cell.strip() for cell in row):
                    continue
                try:
                    apps.append(
                        AppJob(
                            category=row[index["category"]].strip(),
                            sub_category=row[index["sub_category"]].strip(),
                            app_name=row[index["app_name"]].strip(),
                            package_id=row[index["package_id"]].strip(),
                            source=row[index["source"]].strip(),
                            priority=row[index["priority"]].strip(),
                            notes=row[index["notes"]].strip() if len(row) > index["notes"] else "",
                        )
                    )
                except IndexError:
                    logger.warning(f"{path}:{line_no} skipped — malformed row: {row!r}")

        logger.info(f"Loaded {len(apps)} apps from {path}")
        return cls(apps)

    def filter(
        self,
        categories: list[str] | None = None,
        priorities: list[str] | None = None,
    ) -> list[AppJob]:
        """Return apps matching the given filters (case-insensitive, whitespace-trimmed)."""
        cat_set = self._prepare_filter("category", categories, self.categories())
        pri_set = self._prepare_filter("priority", priorities, self.priorities())

        result: list[AppJob] = []
        for app in self._apps:
            if cat_set is not None and _normalize(app.category) not in cat_set:
                continue
            if pri_set is not None and _normalize(app.priority) not in pri_set:
                continue
            result.append(app)
        return result

    def categories(self) -> list[str]:
        return sorted({a.category for a in self._apps})

    def priorities(self) -> list[str]:
        return sorted({a.priority for a in self._apps})

    def _prepare_filter(
        self,
        field_name: str,
        requested: list[str] | None,
        known: list[str],
    ) -> set[str] | None:
        if requested is None:
            return None
        normalized = {_normalize(v) for v in requested}
        known_norm = {_normalize(v) for v in known}
        unknown = normalized - known_norm
        if unknown:
            logger.warning(
                f"AppCatalog.filter: unknown {field_name} values ignored: {sorted(unknown)}"
            )
        return normalized


__all__ = ["AppCatalog", "AppJob"]


# Sanity check: every AppJob field is covered by _REQUIRED_COLUMNS.
assert {f.name for f in fields(AppJob)} == set(_REQUIRED_COLUMNS)
