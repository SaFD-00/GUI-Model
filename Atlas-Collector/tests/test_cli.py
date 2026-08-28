"""CLI wiring — especially that ``--config`` is not a no-op.

``--config`` used to be a decorative flag: ``atlas-collect --config /nonexistent/nope.yaml
catalog --stats`` exited 0 and printed normally, because ``cli.py`` never imported
``config`` at all. Exit-code-0 tests do not catch that, so the tests here assert on
the RESOLVED config object that ``main()`` attaches to the namespace. Delete the
``load_run_config()`` call from ``main()`` and
``test_explicit_config_file_actually_reaches_the_command`` fails immediately.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from atlas_collector import cli


@pytest.fixture(autouse=True)
def _no_ac_env(monkeypatch):
    """``AC_*`` env vars outrank the file; strip them so the assertions are stable."""
    for key in list(os.environ):
        if key.startswith("AC_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def spy(monkeypatch):
    """Replace ``cmd_catalog`` with a recorder and hand back the namespace it saw.

    ``build_parser()`` runs inside ``main()``, so ``set_defaults(func=cmd_catalog)``
    resolves the module global after this patch is installed.
    """
    seen: dict[str, object] = {}

    def _record(args):
        seen["args"] = args
        return 0

    monkeypatch.setattr(cli, "cmd_catalog", _record)
    return seen


def write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "custom.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# --config is wired: the resolved config reaches the command
# ---------------------------------------------------------------------------


def test_explicit_config_file_actually_reaches_the_command(tmp_path, spy):
    """The anti-no-op test: the file's VALUE must land, not merely exit 0."""
    path = write_yaml(tmp_path, "collection:\n  max_steps: 7\ndevice:\n  width: 1440\n")

    assert cli.main(["--config", str(path), "catalog", "--stats"]) == 0

    cfg = spy["args"].run_config
    assert cfg.source_path == path, "the named file must be the one that was read"
    assert cfg.collection.max_steps == 7, "the file's value must survive to the command"
    assert cfg.device.width == 1440
    assert cfg.collection.seed == 42  # unnamed keys keep builtin defaults


def test_default_config_run_yaml_is_used_when_no_flag_is_given(spy):
    assert cli.main(["catalog", "--stats"]) == 0
    cfg = spy["args"].run_config
    assert cfg.source_path is not None
    assert cfg.source_path.name == "run.yaml"
    assert cfg.collection.max_steps == 1500


def test_absent_default_file_falls_back_to_builtin_defaults(tmp_path, spy, monkeypatch):
    monkeypatch.setattr(
        "atlas_collector.config.config_path", lambda *a, **k: tmp_path / "absent.yaml"
    )
    assert cli.main(["catalog", "--stats"]) == 0
    cfg = spy["args"].run_config
    assert cfg.source_path is None, "no file was read"
    assert cfg.collection.max_steps == 1500, "builtin defaults stand"


def test_absent_default_file_is_not_warned_about(tmp_path, monkeypatch, capsys):
    """The fallback must be SILENT — and the command must really run.

    No spy here on purpose: the real ``cmd_catalog`` writes to stdout, so an empty
    stderr means "nothing was warned" rather than "nothing happened at all".
    """
    monkeypatch.setattr(
        "atlas_collector.config.config_path", lambda *a, **k: tmp_path / "absent.yaml"
    )
    assert cli.main(["catalog", "--stats"]) == 0
    out, err = capsys.readouterr()
    assert "catalog: 52 rows total" in out, "the command must have actually run"
    assert err == "", "a missing DEFAULT file must not warn"


# ---------------------------------------------------------------------------
# --config fails loudly
# ---------------------------------------------------------------------------


def test_missing_config_path_exits_nonzero_with_a_clear_message(tmp_path, capsys):
    missing = tmp_path / "nope.yaml"

    code = cli.main(["--config", str(missing), "catalog", "--stats"])

    assert code == 2
    out, err = capsys.readouterr()
    assert out == "", "the command must not run at all on a bad config"
    assert "nope.yaml" in err, "the message must name the path the operator typed"
    assert "not found" in err


def test_unparseable_config_exits_nonzero(tmp_path, capsys):
    path = write_yaml(tmp_path, "collection:\n  max_steps: [1, 2\n")
    assert cli.main(["--config", str(path), "catalog", "--stats"]) == 2
    assert "not valid YAML" in capsys.readouterr().err


def test_typod_config_section_exits_nonzero(tmp_path, capsys):
    path = write_yaml(tmp_path, "collectoin:\n  max_steps: 7\n")
    assert cli.main(["--config", str(path), "catalog", "--stats"]) == 2
    assert "collectoin" in capsys.readouterr().err


def test_out_of_range_config_value_exits_nonzero(tmp_path, capsys):
    path = write_yaml(tmp_path, "device:\n  width: 0\n")
    assert cli.main(["--config", str(path), "catalog", "--stats"]) == 2
    assert "device.width" in capsys.readouterr().err


def test_bad_config_is_rejected_before_the_subcommand_check(tmp_path, capsys):
    """Even with no subcommand, a typo'd --config must not be swallowed."""
    assert cli.main(["--config", str(tmp_path / "nope.yaml")]) == 2
    assert "nope.yaml" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# catalog end-to-end (real config, real CSV)
# ---------------------------------------------------------------------------


def test_catalog_stats_runs_end_to_end(capsys):
    assert cli.main(["catalog", "--stats"]) == 0
    out = capsys.readouterr().out
    assert "catalog: 52 rows total" in out
    assert "clone_accepted=2" in out


def test_catalog_can_select_the_clone_accepted_rows(capsys):
    assert cli.main(["catalog", "--status", "clone_accepted"]) == 0
    out = capsys.readouterr().out
    assert "com.emijotify.feesound" in out
    assert "com.yummely.app" in out


def test_retired_status_values_are_no_longer_offered():
    """`--status` must not advertise statuses no row can have."""
    with pytest.raises(SystemExit):
        cli.main(["catalog", "--status", "pending_adjudication"])


# ---------------------------------------------------------------------------
# Milestone strings (M1+M2 done, M3-M5 pending)
# ---------------------------------------------------------------------------


def test_help_epilog_marks_m1_and_m2_done():
    epilog = cli.build_parser().epilog or ""
    assert "M1 scaffold (DONE)" in epilog
    assert "M2 xml encoding + coordinate frame (DONE)" in epilog
    for pending in ("M3 exploration", "M4 collection loop", "M5 Stage-1 export"):
        assert pending in epilog


def test_package_docstring_marks_m2_done():
    import atlas_collector

    doc = atlas_collector.__doc__ or ""
    assert "NOT IMPLEMENTED (``xml`` is empty)" not in doc
    assert "M2 xml/ encoding + coordinate frame    — DONE" in doc


def test_every_subcommand_is_implemented():
    """The table emptied out as the milestones landed: sync-installed and
    provision in M3, run in M4, export in M5. Kept so a future subcommand has
    one place to register rather than reinventing the pattern."""
    assert cli.UNIMPLEMENTED == {}


# ---------------------------------------------------------------------------
# reset — one root, one removal
# ---------------------------------------------------------------------------


def _populate(root):
    """A collection root with all three artifact kinds present."""
    (root / "raw" / "com.a" / "observations" / "0000").mkdir(parents=True)
    (root / "raw" / "com.a" / "observations" / "0000" / "screenshot.png").write_bytes(b"x" * 10)
    (root / "raw" / "com.a" / "triples.jsonl").write_text("{}\n")
    (root / "runtime" / "apps" / "com.a").mkdir(parents=True)
    (root / "runtime" / "apps" / "com.a" / "cost.csv").write_text("h\n")
    (root / "images").mkdir()
    (root / "images" / "episode_com.a_step_0000.jpg").write_bytes(b"y" * 10)
    (root / "stage1_train.jsonl").write_text("{}\n")
    (root / "export_meta.json").write_text("{}")


def test_reset_dry_run_deletes_nothing(tmp_path, capsys):
    root = tmp_path / "AtlasCollection"
    root.mkdir()
    _populate(root)
    assert cli.main(["reset", "--root", str(root), "--all", "--dry-run"]) == 0
    assert "dry-run" in capsys.readouterr().out
    assert (root / "raw" / "com.a" / "triples.jsonl").exists()
    assert (root / "stage1_train.jsonl").exists()


def test_reset_all_clears_every_artifact_kind(tmp_path):
    """Throwing a pilot away must be ONE action.

    Three roots meant three removals that all had to be remembered, and a
    forgotten `raw/` beside a fresh export is indistinguishable from a
    consistent one — while a resumed session would silently continue the old
    observation numbering.
    """
    root = tmp_path / "AtlasCollection"
    root.mkdir()
    _populate(root)
    assert cli.main(["reset", "--root", str(root), "--all"]) == 0
    assert not (root / "raw").exists()
    assert not (root / "runtime").exists()
    assert not (root / "images").exists()
    assert list(root.glob("stage1_*.jsonl")) == []
    assert not (root / "export_meta.json").exists()


def test_reset_scopes_are_independent(tmp_path):
    root = tmp_path / "AtlasCollection"
    root.mkdir()
    _populate(root)
    assert cli.main(["reset", "--root", str(root), "--export"]) == 0
    assert list(root.glob("stage1_*.jsonl")) == []
    assert not (root / "images").exists()
    assert (root / "raw" / "com.a" / "triples.jsonl").exists(), "raw is a separate scope"
    assert (root / "runtime" / "apps" / "com.a").exists()


def test_reset_without_a_scope_refuses_rather_than_guessing(tmp_path):
    root = tmp_path / "AtlasCollection"
    root.mkdir()
    _populate(root)
    assert cli.main(["reset", "--root", str(root)]) == 2
    assert (root / "raw").exists(), "a scopeless reset must delete nothing"


def test_reset_on_a_missing_root_is_not_an_error(tmp_path):
    assert cli.main(["reset", "--root", str(tmp_path / "absent"), "--all"]) == 0
