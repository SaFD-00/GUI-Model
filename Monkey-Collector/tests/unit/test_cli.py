"""CLI wiring — especially that ``--config`` is not a no-op.

``--config`` could easily become a decorative flag: ``monkey-collect --config
/nonexistent/nope.yaml catalog --stats`` exiting 0 and printing normally would
mean ``cli.py`` never actually read ``config``. Exit-code-0 tests do not catch
that, so the tests here assert on the RESOLVED config object that ``main()``
attaches to the namespace. Delete the ``load_run_config()`` call from
``main()`` and ``test_explicit_config_file_actually_reaches_the_command``
fails immediately.

Host-pull rebuild: six subcommands are wired — ``catalog``, ``sync-installed``,
``provision``, ``reset``, ``run`` and ``export`` — because each has a working
implementation behind it. Nothing is registered ahead of its code (see
``cli.py``'s module docstring for why no ``NotImplementedError`` placeholder is
used either).

Nothing here touches a device: ``run``'s tests drive :class:`tests.fakes.FakeAdb`
and stub the LLM client away, because the collection target is a real,
logged-in phone (AGENTS §0.5/§1).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from monkey_collector import cli
from monkey_collector.config import load_run_config


@pytest.fixture(autouse=True)
def _no_mc_env(monkeypatch):
    """``MC_*`` env vars outrank the file; strip them so the assertions are stable."""
    for key in list(os.environ):
        if key.startswith("MC_"):
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
# no subcommand
# ---------------------------------------------------------------------------


def test_no_command_prints_help_and_returns_one(capsys):
    assert cli.main([]) == 1
    assert "usage: monkey-collect" in capsys.readouterr().out


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
        "monkey_collector.config.config_path", lambda *a, **k: tmp_path / "absent.yaml"
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
        "monkey_collector.config.config_path", lambda *a, **k: tmp_path / "absent.yaml"
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
    assert "collectable    : 48" in out


def test_catalog_can_select_the_excluded_rows(capsys):
    assert cli.main(["catalog", "--status", "excluded"]) == 0
    out = capsys.readouterr().out
    assert "4 / 52 rows" in out


def test_unknown_status_is_not_offered():
    """`--status` must not advertise a status no row can have."""
    with pytest.raises(SystemExit):
        cli.main(["catalog", "--status", "pending_adjudication"])


# ---------------------------------------------------------------------------
# run and export are both registered (M5)
# ---------------------------------------------------------------------------


def test_export_is_a_registered_subcommand_with_the_flags_the_export_needs():
    args = cli.build_parser().parse_args(
        [
            "export", "--root", "/tmp/root", "--seed", "3",
            "--keep-unchanged", "--ood-apps", "0.4", "--id-ratio", "0.2",
        ]
    )
    assert args.func is cli.cmd_export
    assert (args.root, args.seed, args.keep_unchanged) == ("/tmp/root", 3, True)
    assert (args.ood_apps, args.id_ratio) == (0.4, 0.2)


def test_export_defaults_leave_the_split_knobs_to_the_config():
    args = cli.build_parser().parse_args(["export"])
    assert args.keep_unchanged is False, "unchanged triples are opt-in"
    assert args.ood_apps is None and args.id_ratio is None, "None means: use export.*"
    assert args.seed is None


def test_export_flags_outrank_the_config_and_reach_the_exporter(tmp_path, monkeypatch):
    """A flag that is parsed and then ignored is worse than one that is missing."""
    seen = {}

    class _Recorder:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            seen["dirs"] = args

        def run(self):
            from monkey_collector.export import ExportStats

            return ExportStats()

    import monkey_collector.export as export_module

    monkeypatch.setattr(export_module, "Exporter", _Recorder)
    # Nothing was written, so the empty-result exit code is expected.
    assert cli.main(
        ["export", "--root", str(tmp_path), "--seed", "5",
         "--ood-apps", "0.4", "--id-ratio", "0.2", "--keep-unchanged"]
    ) == 1
    assert (seen["ood_apps"], seen["id_ratio"], seen["seed"]) == (0.4, 0.2, 5)
    assert seen["keep_unchanged"] is True
    assert seen["target_size"] == (840, 1876), "the contract frame comes from export.*"
    assert seen["device_size"] == (1080, 2400), "only a fallback; the session's size wins"


def test_export_without_the_flags_takes_the_configured_values(tmp_path, monkeypatch):
    seen = {}

    class _Recorder:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)

        def run(self):
            from monkey_collector.export import ExportStats

            return ExportStats()

    import monkey_collector.export as export_module

    monkeypatch.setattr(export_module, "Exporter", _Recorder)
    assert cli.main(["export", "--root", str(tmp_path)]) == 1
    assert (seen["ood_apps"], seen["id_ratio"]) == (0.3, 0.1)
    assert seen["seed"] == 42, "collection.seed"


def test_run_is_registered_with_the_flags_the_runner_needs():
    args = cli.build_parser().parse_args(
        [
            "run", "--apps", "com.a", "com.b", "--serial", "SER",
            "--budget-mode", "steps", "--max-duration", "1h", "--max-steps", "5",
            "--seed", "7", "--input-mode", "random", "--root", "/tmp/root",
            "--force", "--include-auth", "--no-prepare-device",
        ]
    )
    assert args.func is cli.cmd_run
    assert args.apps == ["com.a", "com.b"]
    assert (args.serial, args.budget_mode, args.max_duration) == ("SER", "steps", "1h")
    assert (args.max_steps, args.seed, args.input_mode) == (5, 7, "random")
    assert (args.root, args.force, args.include_auth) == ("/tmp/root", True, True)
    assert args.prepare_device is False


def test_run_defaults_are_the_safe_ones():
    args = cli.build_parser().parse_args(["run"])
    assert args.apps == ["all"]
    assert args.include_auth is False, "account_required apps are opt-in (AGENTS §3)"
    assert args.force is False
    assert args.prepare_device is True, "the device preconditions are the default"


def test_help_epilog_names_every_implemented_subcommand():
    epilog = cli.build_parser().epilog or ""
    assert "Implemented: catalog, sync-installed, provision, reset, run, review, export." in epilog
    assert "NOT implemented" not in epilog, "M6 shipped review; the epilog must not lag"
    assert "action guard" in epilog, "AGENTS §0.5 is a warning, not a footnote"


def test_module_docstring_lists_export_as_implemented():
    doc = cli.__doc__ or ""
    assert "does NOT exist yet" not in doc
    assert "export" in doc


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
    root = tmp_path / "MonkeyCollection"
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
    root = tmp_path / "MonkeyCollection"
    root.mkdir()
    _populate(root)
    (root / "run.log").write_text("sweep started\n")
    assert cli.main(["reset", "--root", str(root), "--all"]) == 0
    assert not (root / "raw").exists()
    assert not (root / "runtime").exists()
    assert not (root / "images").exists()
    assert list(root.glob("stage1_*.jsonl")) == []
    assert not (root / "export_meta.json").exists()
    assert (root / "run.log").exists(), "the record of what went wrong survives --all"


def test_reset_scopes_are_independent(tmp_path):
    root = tmp_path / "MonkeyCollection"
    root.mkdir()
    _populate(root)
    assert cli.main(["reset", "--root", str(root), "--export"]) == 0
    assert list(root.glob("stage1_*.jsonl")) == []
    assert not (root / "images").exists()
    assert (root / "raw" / "com.a" / "triples.jsonl").exists(), "raw is a separate scope"
    assert (root / "runtime" / "apps" / "com.a").exists()


def test_reset_without_a_scope_refuses_rather_than_guessing(tmp_path):
    root = tmp_path / "MonkeyCollection"
    root.mkdir()
    _populate(root)
    assert cli.main(["reset", "--root", str(root)]) == 2
    assert (root / "raw").exists(), "a scopeless reset must delete nothing"


def test_reset_on_a_missing_root_is_not_an_error(tmp_path):
    assert cli.main(["reset", "--root", str(tmp_path / "absent"), "--all"]) == 0


# ---------------------------------------------------------------------------
# run — target selection, device preparation, and one whole sweep
# ---------------------------------------------------------------------------

TARGET = "net.gsantner.markor"  # collectable, auth_required=none


def test_account_required_apps_are_skipped_by_default():
    """AGENTS §3: a session stuck against a login wall spends its budget on the
    login wall. They are opt-in, and the default must not be flipped."""
    from monkey_collector.catalog import load_catalog

    rows = load_catalog()
    targets, skipped = cli.select_targets(rows, set(), include_auth=False)
    assert targets, "the sweep still has work to do"
    assert not any(row.needs_auth for row in targets)
    assert skipped and all(row.needs_auth for row in skipped)
    assert all(row.is_collectable for row in targets), "excluded rows are never driven"


def test_include_auth_admits_the_account_required_apps():
    from monkey_collector.catalog import load_catalog

    rows = load_catalog()
    default, skipped = cli.select_targets(rows, set(), include_auth=False)
    opened, none_skipped = cli.select_targets(rows, set(), include_auth=True)
    assert none_skipped == []
    assert len(opened) == len(default) + len(skipped)
    assert any(row.needs_auth for row in opened)


def test_selecting_one_app_still_excludes_the_uncollectable():
    from monkey_collector.catalog import load_catalog

    rows = load_catalog()
    excluded = next(row for row in rows if row.is_excluded)
    targets, _ = cli.select_targets(rows, {excluded.package_id}, include_auth=True)
    assert targets == []


def test_prepare_device_applies_both_preconditions_and_probes_focus():
    """AGENTS §1: a locked screen makes every dump `com.android.systemui`, and
    the GMS update modal makes it `com.google.android.gms` with a single
    "Download & install now" button in front of a guard-free explorer."""
    from tests.fakes import FakeAdb, Script, screen

    adb = FakeAdb([Script(screen())])
    cli.prepare_device(adb)
    assert "svc power stayon true" in adb.shell_commands
    assert "settings put global ota_disable_automatic_update 1" in adb.shell_commands
    assert cli.FOCUS_PROBE in adb.shell_commands


def test_prepare_device_survives_a_refused_command():
    """`grep` exits non-zero when it matches nothing, and `shell` raises on that."""
    from tests.fakes import FakeAdb, Script, screen

    class Grumpy(FakeAdb):
        def shell(self, command, **kwargs):
            super().shell(command, **kwargs)
            raise RuntimeError("device says no")

    adb = Grumpy([Script(screen())])
    lines = cli.prepare_device(adb)
    # Derived, not a literal: adding a precondition must not need this edited.
    expected = len(cli.DEVICE_PREPARATION) + 1  # + the focus probe
    assert len(adb.shell_commands) == expected, "one refusal must not stop the others"
    assert all(command in adb.shell_commands for _, command in cli.DEVICE_PREPARATION)
    assert any("FAILED" in line for line in lines)


@pytest.fixture
def sweep(monkeypatch, tmp_path):
    """Run `monkey-collect run` against a fake device, with no LLM and no ADB."""
    from monkey_collector.llm import client as llm_client
    from tests.fakes import FakeAdb, Script, screen

    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    # The labeller would otherwise issue real API calls for every new structure.
    monkeypatch.setattr(llm_client, "create_llm_client", lambda *a, **k: None)

    adb = FakeAdb(
        [Script(screen(package=TARGET, tag=t), f"{TARGET}/.Main") for t in "abcdef"]
    )
    monkeypatch.setattr(
        "monkey_collector.adb.AdbClient", lambda serial=None, **kwargs: adb
    )
    config = tmp_path / "run.yaml"
    config.write_text(
        "collection:\n  action_delay_ms: 0\n  stabilize_max_wait_sec: 0.5\n  launch_settle_sec: 0\n",
        encoding="utf-8",
    )
    root = tmp_path / "MonkeyCollection"

    def _run(*extra: str) -> int:
        return cli.main(
            [
                "--config", str(config), "run", "--root", str(root),
                "--apps", TARGET, "--budget-mode", "steps", "--max-steps", "2",
                *extra,
            ]
        )

    return adb, root, _run


def test_a_sweep_writes_the_corpus_under_one_root(sweep):
    adb, root, run = sweep
    assert run("--no-prepare-device") == 0

    app_root = root / "raw" / TARGET
    assert (app_root / "triples.jsonl").is_file()
    assert (app_root / "graph.json").is_file()
    assert (app_root / "metadata.json").is_file()
    assert (app_root / "observations" / "0000" / "screenshot.png").is_file()
    assert (root / "runtime" / "apps" / TARGET / "activity_coverage.csv").is_file()
    assert (root / "runtime" / "apps" / TARGET / "cost.csv").is_file()
    assert (root / "run.log").is_file(), "the sweep's log belongs in the root"
    assert TARGET in adb.stopped, "every catalog app is stopped before the sweep"
    assert adb.actions[0] == ("launch", (TARGET, True)), "the app starts cold"


def test_a_sweep_prepares_the_device_by_default(sweep):
    adb, _, run = sweep
    assert run() == 0
    assert "svc power stayon true" in adb.shell_commands
    assert "settings put global ota_disable_automatic_update 1" in adb.shell_commands


def test_no_prepare_device_skips_the_preparation(sweep):
    adb, _, run = sweep
    assert run("--no-prepare-device") == 0
    assert not any("stayon" in command for command in adb.shell_commands)
    assert not any("ota_disable" in command for command in adb.shell_commands)


def test_a_completed_app_is_skipped_unless_forced(sweep, capsys):
    _, root, run = sweep
    triples = root / "raw" / TARGET / "triples.jsonl"
    assert run("--no-prepare-device") == 0
    capsys.readouterr()

    assert run("--no-prepare-device") == 0
    assert "already complete, skipping" in capsys.readouterr().out

    triples.unlink()  # so the forced run has to write them again to pass
    assert run("--no-prepare-device", "--force") == 0
    assert "already complete" not in capsys.readouterr().out
    assert triples.is_file() and triples.read_text().strip(), "--force really re-collected"


def test_a_resumed_session_keeps_its_coverage_time_series(sweep):
    """`initialize` opens the CSV with "w". Truncating it on resume would leave
    a plausible curve that starts from zero, with nothing to say why."""
    _, root, run = sweep
    coverage = root / "runtime" / "apps" / TARGET / "activity_coverage.csv"
    assert run("--no-prepare-device") == 0
    first = coverage.read_text().splitlines()

    # A session killed mid-flight: metadata says nothing was collected, disk
    # disagrees, and `completed_at` is null so the next sweep resumes it.
    meta_path = root / "raw" / TARGET / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta.update({"completed_at": None, "observations": 0, "steps": 0})
    meta_path.write_text(json.dumps(meta))

    # A bigger budget than the first run's, because the step budget is
    # CUMULATIVE across resumes -- `--max-steps 2` again would end instantly.
    assert run("--no-prepare-device", "--max-steps", "4") == 0
    rows = coverage.read_text().splitlines()
    assert rows[: len(first)] == first, "the first run's rows are still there, unchanged"
    assert len(rows) > len(first), "and the resumed run appended to them"


def test_input_mode_reaches_the_text_generator(monkeypatch, sweep):
    """The flag is CONSUMED, not merely parsed: the sibling collector parses
    --input-mode and never reads it again."""
    from monkey_collector import text_input

    seen: list[str] = []
    real = text_input.create_text_generator

    def spy(llm_config, **kwargs):
        seen.append(llm_config.input_mode)
        return real(llm_config, **kwargs)

    monkeypatch.setattr(text_input, "create_text_generator", spy)
    _, _, run = sweep
    assert run("--no-prepare-device", "--input-mode", "random") == 0
    assert seen == ["random"], "the resolved config carries the flag"


# ---------------------------------------------------------------------------
# review (M6)
# ---------------------------------------------------------------------------


def test_review_is_a_registered_subcommand():
    args = cli.build_parser().parse_args(["review"])

    assert args.command == "review"
    assert args.func is cli.cmd_review


def test_review_binds_loopback_by_default():
    # The corpus is screenshots of a REAL, logged-in device (AGENTS §0.5).
    # Any other default would publish it to the network.
    args = cli.build_parser().parse_args(["review"])

    assert args.host == "127.0.0.1"
    assert args.port == 8700


def test_review_takes_a_reviewer_and_a_root():
    args = cli.build_parser().parse_args(
        ["review", "--root", "data/Other", "--reviewer", "kim", "--no-browser"]
    )

    assert args.root == "data/Other"
    assert args.reviewer == "kim"
    assert args.no_browser is True


def test_review_writes_to_the_review_subtree_of_the_root_it_was_given(monkeypatch):
    seen = {}

    def fake_serve(raw, review, **kwargs):
        seen["raw"] = Path(raw)
        seen["review"] = Path(review)
        seen.update(kwargs)
        return 0

    import monkey_collector.review.server as server_module

    monkeypatch.setattr(server_module, "serve", fake_serve)
    args = cli.build_parser().parse_args(["review", "--root", str(Path("data/Pilot"))])
    args.run_config = load_run_config(None)

    assert cli.cmd_review(args) == 0
    assert seen["raw"].name == "raw"
    assert seen["review"].name == "review"
    assert seen["raw"].parent == seen["review"].parent


def test_export_applies_review_verdicts_unless_told_not_to():
    default = cli.build_parser().parse_args(["export"])
    ignored = cli.build_parser().parse_args(["export", "--ignore-review"])

    # Applied by DEFAULT: a filter that must be opted into is a filter that gets
    # forgotten, and the export it produces is silently the unfiltered one.
    assert default.ignore_review is False
    assert default.strict_review is False
    assert ignored.ignore_review is True
