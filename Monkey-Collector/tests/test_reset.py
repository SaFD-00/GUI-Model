"""Tests for reset: delete collected session data by scope."""

from __future__ import annotations

from pathlib import Path

import pytest

from monkey_collector.pipeline.reset import delete_targets, resolve_targets


def _seed(root: Path, package: str) -> Path:
    d = root / package
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text("{}")
    return d


class TestResolveTargets:
    def test_all_returns_output_root(self, tmp_path):
        _seed(tmp_path, "com.a")
        targets = resolve_targets(tmp_path, all_=True)
        assert targets == [tmp_path]

    def test_all_excludes_missing_output(self, tmp_path):
        missing = tmp_path / "nonexistent"
        targets = resolve_targets(missing, all_=True)
        assert targets == []

    def test_packages_returns_matching_dirs(self, tmp_path):
        _seed(tmp_path, "com.a")
        _seed(tmp_path, "com.b")
        _seed(tmp_path, "com.c")
        targets = resolve_targets(tmp_path, packages=["com.a", "com.c"])
        assert sorted(targets) == sorted([tmp_path / "com.a", tmp_path / "com.c"])

    def test_packages_filters_out_nonexistent(self, tmp_path):
        _seed(tmp_path, "com.a")
        targets = resolve_targets(tmp_path, packages=["com.a", "com.missing"])
        assert targets == [tmp_path / "com.a"]

    def test_no_scope_raises(self, tmp_path):
        with pytest.raises(ValueError, match="scope"):
            resolve_targets(tmp_path)


class TestDeleteTargets:
    def test_removes_dirs(self, tmp_path):
        d1 = _seed(tmp_path, "com.a")
        d2 = _seed(tmp_path, "com.b")
        count = delete_targets([d1, d2], dry_run=False)
        assert count == 2
        assert not d1.exists()
        assert not d2.exists()

    def test_dry_run_keeps_dirs(self, tmp_path):
        d = _seed(tmp_path, "com.a")
        count = delete_targets([d], dry_run=True)
        assert count == 0
        assert d.exists()

    def test_ignores_missing(self, tmp_path):
        count = delete_targets([tmp_path / "gone"], dry_run=False)
        assert count == 0
