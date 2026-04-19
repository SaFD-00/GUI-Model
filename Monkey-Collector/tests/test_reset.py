"""Tests for reset: delete collected session data by scope."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from server.pipeline.reset import delete_targets, resolve_targets


def _seed(root: Path, category: str, package: str) -> Path:
    d = root / category / package
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text("{}")
    return d


def _apps_csv(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    """rows: (category, package_id, priority)"""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "category", "sub_category", "app_name",
            "package_id", "source", "priority", "notes",
        ])
        for cat, pkg, pri in rows:
            writer.writerow([cat, "", pkg, pkg, "PlayStore", pri, ""])
    return path


class TestResolveTargets:
    def test_all_returns_output_root(self, tmp_path):
        _seed(tmp_path, "Shopping", "com.a")
        targets = resolve_targets(tmp_path, all_=True)
        assert targets == [tmp_path]

    def test_all_excludes_missing_output(self, tmp_path):
        missing = tmp_path / "nonexistent"
        targets = resolve_targets(missing, all_=True)
        assert targets == []

    def test_categories_returns_category_dirs(self, tmp_path):
        _seed(tmp_path, "Shopping", "com.a")
        _seed(tmp_path, "Media", "com.b")
        targets = resolve_targets(tmp_path, categories=["Shopping"])
        assert targets == [tmp_path / "Shopping"]

    def test_categories_filters_out_nonexistent(self, tmp_path):
        _seed(tmp_path, "Shopping", "com.a")
        targets = resolve_targets(tmp_path, categories=["Shopping", "Gone"])
        assert targets == [tmp_path / "Shopping"]

    def test_packages_searches_all_categories(self, tmp_path):
        _seed(tmp_path, "Shopping", "com.target")
        _seed(tmp_path, "Media", "com.target")
        _seed(tmp_path, "Shopping", "com.other")
        targets = resolve_targets(tmp_path, packages=["com.target"])
        assert sorted(targets) == sorted([
            tmp_path / "Shopping" / "com.target",
            tmp_path / "Media" / "com.target",
        ])

    def test_categories_and_packages_intersect(self, tmp_path):
        _seed(tmp_path, "Shopping", "com.a")
        _seed(tmp_path, "Media", "com.a")
        targets = resolve_targets(
            tmp_path, categories=["Shopping"], packages=["com.a"],
        )
        assert targets == [tmp_path / "Shopping" / "com.a"]

    def test_apps_csv_filter_resolves_packages(self, tmp_path):
        csv_path = _apps_csv(tmp_path / "apps.csv", [
            ("Shopping", "com.keep", "High"),
            ("Shopping", "com.drop", "Low"),
            ("Media", "com.keep", "High"),
        ])
        _seed(tmp_path, "Shopping", "com.keep")
        _seed(tmp_path, "Shopping", "com.drop")
        _seed(tmp_path, "Media", "com.keep")
        targets = resolve_targets(
            tmp_path, apps_csv=csv_path, priorities=["High"],
        )
        assert sorted(targets) == sorted([
            tmp_path / "Shopping" / "com.keep",
            tmp_path / "Media" / "com.keep",
        ])

    def test_no_scope_raises(self, tmp_path):
        with pytest.raises(ValueError, match="scope"):
            resolve_targets(tmp_path)


class TestDeleteTargets:
    def test_removes_dirs(self, tmp_path):
        d1 = _seed(tmp_path, "Shopping", "com.a")
        d2 = _seed(tmp_path, "Shopping", "com.b")
        count = delete_targets([d1, d2], dry_run=False)
        assert count == 2
        assert not d1.exists()
        assert not d2.exists()

    def test_dry_run_keeps_dirs(self, tmp_path):
        d = _seed(tmp_path, "Shopping", "com.a")
        count = delete_targets([d], dry_run=True)
        assert count == 0
        assert d.exists()

    def test_ignores_missing(self, tmp_path):
        count = delete_targets([tmp_path / "gone"], dry_run=False)
        assert count == 0
