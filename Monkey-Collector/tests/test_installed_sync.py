"""Tests for monkey_collector.pipeline.installed_sync."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from monkey_collector.pipeline.installed_sync import (
    list_installed_packages,
    sync_installed_column,
)


def test_list_installed_packages_parses_pm_output() -> None:
    adb = MagicMock()
    adb.shell.return_value = (
        "package:com.android.settings\n"
        "package:com.google.android.gm\n"
        "package:   \n"
        "unrelated noise\n"
        "package:com.whatsapp"
    )
    result = list_installed_packages(adb)
    assert result == {
        "com.android.settings",
        "com.google.android.gm",
        "com.whatsapp",
    }
    adb.shell.assert_called_once_with("pm list packages")


def test_sync_adds_installed_column_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "apps.csv"
    path.write_text(
        "category,sub_category,app_name,package_id,source,priority,notes\n"
        "A,B,One,com.one,PlayStore,High,\n"
        "A,B,Two,com.two,PlayStore,Medium,\n",
        encoding="utf-8",
    )
    total, installed, changed = sync_installed_column(path, {"com.one"})

    assert total == 2
    assert installed == 1
    # Column added implies all rows changed vs the default "false" for missing column.
    assert changed == 1

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith(",installed")
    assert lines[1].endswith(",true")
    assert lines[2].endswith(",false")


def test_sync_updates_existing_installed_column(tmp_path: Path) -> None:
    path = tmp_path / "apps.csv"
    path.write_text(
        "category,sub_category,app_name,package_id,source,priority,notes,installed\n"
        "A,B,One,com.one,PlayStore,High,,false\n"
        "A,B,Two,com.two,PlayStore,Medium,,true\n"
        "A,B,Three,com.three,PlayStore,Low,,false\n",
        encoding="utf-8",
    )
    total, installed, changed = sync_installed_column(
        path, {"com.one", "com.three"}
    )

    assert total == 3
    assert installed == 2
    # com.one: false→true, com.two: true→false, com.three: false→true
    assert changed == 3

    content = path.read_text(encoding="utf-8")
    assert "A,B,One,com.one,PlayStore,High,,true" in content
    assert "A,B,Two,com.two,PlayStore,Medium,,false" in content
    assert "A,B,Three,com.three,PlayStore,Low,,true" in content


def test_sync_preserves_unrelated_fields(tmp_path: Path) -> None:
    path = tmp_path / "apps.csv"
    path.write_text(
        "category,sub_category,app_name,package_id,source,priority,notes,installed\n"
        "Shopping,General,Amazon,com.amazon,PlayStore,High,complex UI flows,false\n",
        encoding="utf-8",
    )
    sync_installed_column(path, {"com.amazon"})
    out = path.read_text(encoding="utf-8")
    assert "Shopping,General,Amazon,com.amazon,PlayStore,High,complex UI flows,true" in out


def test_sync_no_change_when_state_matches(tmp_path: Path) -> None:
    path = tmp_path / "apps.csv"
    path.write_text(
        "category,sub_category,app_name,package_id,source,priority,notes,installed\n"
        "A,B,One,com.one,PlayStore,High,,true\n"
        "A,B,Two,com.two,PlayStore,Medium,,false\n",
        encoding="utf-8",
    )
    total, installed, changed = sync_installed_column(path, {"com.one"})
    assert total == 2
    assert installed == 1
    assert changed == 0


def test_sync_rejects_csv_without_package_id(tmp_path: Path) -> None:
    path = tmp_path / "apps.csv"
    path.write_text("category,app_name\nA,Amazon\n", encoding="utf-8")
    with pytest.raises(ValueError, match="package_id"):
        sync_installed_column(path, set())


def test_sync_handles_bom(tmp_path: Path) -> None:
    path = tmp_path / "apps.csv"
    path.write_text(
        "﻿category,sub_category,app_name,package_id,source,priority,notes,installed\n"
        "A,B,One,com.one,PlayStore,High,,false\n",
        encoding="utf-8",
    )
    total, installed, _ = sync_installed_column(path, {"com.one"})
    assert total == 1
    assert installed == 1
