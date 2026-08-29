"""Config loading and validation.

``config.py`` shipped with zero tests during the device-push era, and three
silent-failure modes came out of that: a device dimension of ``0`` reached the
parser as a raw ``ZeroDivisionError``, ``-5`` produced negative-scaled boxes
without any error at all, and a typo'd YAML SECTION (``collectoin:``) loaded
cleanly while leaving every value at its default — a misconfigured run that
looked perfectly successful.

Every rejection path is asserted to raise :class:`ConfigError` and to NAME the
offending setting, because the whole point is that the operator finds the typo.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from monkey_collector.config import ConfigError, load_run_config, parse_duration

# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_mc_env(monkeypatch):
    """Strip ``MC_*`` overrides.

    ``_apply_env_overrides`` reads the live environment, so a stray
    ``MC_COLLECTION_MAX_STEPS`` in the developer's shell would otherwise make
    every builtin-defaults assertion here flaky.
    """
    for key in list(os.environ):
        if key.startswith("MC_"):
            monkeypatch.delenv(key, raising=False)


def write_yaml(tmp_path: Path, body: str, name: str = "run.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_builtin_defaults_stand_when_no_file_is_given(tmp_path, monkeypatch):
    """An ABSENT default config is not an error — builtin defaults stand, silently."""
    monkeypatch.setattr(
        "monkey_collector.config.config_path", lambda *a, **k: tmp_path / "absent.yaml"
    )
    cfg = load_run_config()
    assert cfg.source_path is None
    assert cfg.device.serial == "19101FDF6004EH"
    assert cfg.device.width == 1080
    assert cfg.device.height == 2400
    assert cfg.collection.budget_mode == "time"
    assert cfg.collection.max_duration == "2h"
    assert cfg.collection.max_duration_sec == 7200
    assert cfg.collection.max_steps == 1500
    assert cfg.page_matching.merge_policy == "similar_elements"
    assert cfg.llm.input_mode == "api"
    assert cfg.export.target_size == (840, 1876)


def test_committed_run_yaml_loads_and_matches_the_builtin_defaults():
    """The real ``config/run.yaml`` must load, and must not drift from the builtins."""
    cfg = load_run_config()
    assert cfg.source_path is not None
    assert cfg.source_path.name == "run.yaml"
    assert cfg.device.serial == "19101FDF6004EH"
    assert cfg.collection.budget_mode == "time"
    assert cfg.collection.max_duration == "2h"
    assert cfg.collection.max_duration_sec == 7200
    assert cfg.collection.max_steps == 1500
    assert cfg.device.width == 1080
    assert cfg.export.target_size == (840, 1876)
    assert cfg.llm.model == "qwen/qwen3.8-flash"


def test_run_yaml_is_exactly_the_builtin_defaults():
    """The whole dict, not a spot-check: a drifted key (e.g. a `run.yaml` edit
    with no matching `_BUILTIN_DEFAULTS` edit, or vice versa) would otherwise
    only surface as a run silently configured differently than the committed
    file reads."""
    import yaml

    from monkey_collector.config import _BUILTIN_DEFAULTS
    from monkey_collector.paths import config_path

    loaded = yaml.safe_load(config_path().read_text(encoding="utf-8"))
    assert loaded == _BUILTIN_DEFAULTS


def test_explicit_file_overrides_only_the_keys_it_names(tmp_path):
    path = write_yaml(tmp_path, "collection:\n  max_steps: 7\n")
    cfg = load_run_config(path)
    assert cfg.source_path == path
    assert cfg.collection.max_steps == 7
    # untouched keys keep their builtin values
    assert cfg.collection.seed == 42
    assert cfg.device.width == 1080


def test_empty_file_and_null_section_are_legal(tmp_path):
    assert load_run_config(write_yaml(tmp_path, "")).collection.max_steps == 1500
    # `device:` with nothing under it is an empty override, not a broken file.
    assert load_run_config(write_yaml(tmp_path, "device:\n", "b.yaml")).device.width == 1080


def test_env_overrides_win_over_the_file(tmp_path, monkeypatch):
    path = write_yaml(tmp_path, "collection:\n  max_steps: 7\n")
    monkeypatch.setenv("MC_COLLECTION_MAX_STEPS", "11")
    assert load_run_config(path).collection.max_steps == 11


def test_env_override_does_not_leak_into_the_builtin_defaults(tmp_path, monkeypatch):
    """An env override with no YAML must not mutate the module-global
    ``_BUILTIN_DEFAULTS`` — both merge steps deepcopy, but this pins the
    observable behaviour rather than the implementation detail."""
    monkeypatch.setattr(
        "monkey_collector.config.config_path", lambda *a, **k: tmp_path / "absent.yaml"
    )
    monkeypatch.setenv("MC_COLLECTION_MAX_STEPS", "999")
    assert load_run_config().collection.max_steps == 999

    monkeypatch.delenv("MC_COLLECTION_MAX_STEPS")
    assert load_run_config().collection.max_steps == 1500


# ---------------------------------------------------------------------------
# device.serial — pinned to the real device, not autodetect
# ---------------------------------------------------------------------------


def test_device_serial_defaults_to_the_pinned_pixel_6():
    """This host also has emulator-5556 attached; autodetect (null) risks
    silently driving the wrong device, so the default is pinned."""
    assert load_run_config().device.serial == "19101FDF6004EH"


def test_device_serial_can_be_overridden_to_autodetect(tmp_path):
    path = write_yaml(tmp_path, "device:\n  serial: null\n")
    assert load_run_config(path).device.serial is None


def test_empty_string_serial_env_is_treated_as_unset(monkeypatch):
    """A stray `MC_DEVICE_SERIAL=` must not pin an empty, unaddressable serial."""
    monkeypatch.setenv("MC_DEVICE_SERIAL", "")
    assert load_run_config().device.serial is None


# ---------------------------------------------------------------------------
# device.width / device.height — the coordinate SOURCE FRAME
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, -5, -1080])
@pytest.mark.parametrize("key", ["width", "height"])
def test_nonpositive_device_dimension_is_rejected(tmp_path, key, value):
    """0 used to detonate as ZeroDivisionError inside the parser; -5 never raised at all."""
    path = write_yaml(tmp_path, f"device:\n  {key}: {value}\n")
    with pytest.raises(ConfigError) as excinfo:
        load_run_config(path)
    assert f"device.{key}" in str(excinfo.value)
    assert "positive int" in str(excinfo.value)


@pytest.mark.parametrize("value", ["'1080'", "1080.5", "true"])
def test_non_integer_device_dimension_is_rejected(tmp_path, value):
    path = write_yaml(tmp_path, f"device:\n  width: {value}\n")
    with pytest.raises(ConfigError, match="device.width"):
        load_run_config(path)


def test_a_valid_device_override_still_loads(tmp_path):
    path = write_yaml(tmp_path, "device:\n  width: 1440\n  height: 3120\n")
    cfg = load_run_config(path)
    assert (cfg.device.width, cfg.device.height) == (1440, 3120)


# ---------------------------------------------------------------------------
# unknown top-level SECTIONS / KEYS are rejected
# ---------------------------------------------------------------------------


def test_typod_section_is_rejected_and_names_itself(tmp_path):
    """The exact audit case: `collectoin:` used to load cleanly with max_steps=1500."""
    path = write_yaml(tmp_path, "collectoin:\n  max_steps: 7\n")
    with pytest.raises(ConfigError) as excinfo:
        load_run_config(path)
    message = str(excinfo.value)
    assert "collectoin" in message
    assert "collection" in message  # the valid-sections list points at the fix


def test_typod_key_inside_a_valid_section_is_still_rejected(tmp_path):
    path = write_yaml(tmp_path, "collection:\n  max_stpes: 7\n")
    with pytest.raises(ConfigError, match="max_stpes"):
        load_run_config(path)


def test_exploration_section_no_longer_exists(tmp_path):
    """The device-push explorer's `exploration.strategy` knob is gone: exploration
    constants land with the explorer itself, in a future milestone."""
    path = write_yaml(tmp_path, "exploration:\n  strategy: BFS\n")
    with pytest.raises(ConfigError, match="exploration"):
        load_run_config(path)


def test_non_mapping_root_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="mapping"):
        load_run_config(write_yaml(tmp_path, "- collection\n- device\n"))


def test_non_mapping_section_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="collection"):
        load_run_config(write_yaml(tmp_path, "collection: 7\n"))


def test_unparseable_yaml_is_rejected(tmp_path):
    path = write_yaml(tmp_path, "collection:\n  max_steps: [1, 2\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_run_config(path)


def test_missing_explicit_path_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_run_config(tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------
# page_matching — LLM-free page identity, ported from LLM-Explorer
# ---------------------------------------------------------------------------


def test_page_matching_defaults_are_the_reference_constants():
    matching = load_run_config().page_matching
    assert matching.merge_policy == "similar_elements"
    assert matching.max_diff_elements == 2
    assert matching.same_activity_only is True


@pytest.mark.parametrize("value", ["structure_only", "similar_elements"])
def test_both_merge_policies_load(tmp_path, value):
    path = write_yaml(tmp_path, f"page_matching:\n  merge_policy: {value}\n")
    assert load_run_config(path).page_matching.merge_policy == value


def test_an_unknown_merge_policy_is_rejected(tmp_path):
    path = write_yaml(tmp_path, "page_matching:\n  merge_policy: bm25_pixel\n")
    with pytest.raises(ConfigError, match="merge_policy"):
        load_run_config(path)


@pytest.mark.parametrize("value", [-1, 0.5, True])
def test_an_invalid_diff_budget_is_rejected(tmp_path, value):
    path = write_yaml(tmp_path, f"page_matching:\n  max_diff_elements: {value}\n")
    with pytest.raises(ConfigError, match="max_diff_elements"):
        load_run_config(path)


def test_a_zero_diff_budget_is_accepted(tmp_path):
    """0 disables similar-element merging without disabling the policy."""
    path = write_yaml(tmp_path, "page_matching:\n  max_diff_elements: 0\n")
    assert load_run_config(path).page_matching.max_diff_elements == 0


def test_pixels_are_not_a_page_matching_knob():
    """Screenshot comparison belongs to stabilization ONLY — page identity via
    BM25/pixel is a Mobile3M mechanism this project does not use."""
    matching = load_run_config().page_matching
    for banned in ("page_pixel_diff_threshold", "element_jaccard_min", "element_diff_max"):
        assert not hasattr(matching, banned), f"{banned} is a Mobile3M knob"


# ---------------------------------------------------------------------------
# collection: host-pull stabilization knobs
# ---------------------------------------------------------------------------


def test_stabilization_defaults_match_the_measured_constants():
    coll = load_run_config().collection
    assert coll.stabilize_poll_ms == 0
    assert coll.stabilize_max_wait_sec == 8.0
    assert coll.stabilize_pixel_threshold == 0.005
    assert coll.stabilize_luma_delta == 10
    assert coll.stabilize_low_res_width == 100


def test_nonpositive_stabilize_max_wait_sec_is_rejected(tmp_path):
    path = write_yaml(tmp_path, "collection:\n  stabilize_max_wait_sec: 0\n")
    with pytest.raises(ConfigError, match="stabilize_max_wait_sec"):
        load_run_config(path)


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_stabilize_pixel_threshold_out_of_unit_interval_is_rejected(tmp_path, value):
    path = write_yaml(tmp_path, f"collection:\n  stabilize_pixel_threshold: {value}\n")
    with pytest.raises(ConfigError, match="stabilize_pixel_threshold"):
        load_run_config(path)


def test_zero_stabilize_pixel_threshold_is_accepted_by_validation():
    """0 is a legal VALUE (the boundary of [0,1]) even though the shipped default
    and run.yaml's comment both warn against actually setting it — a video,
    spinner or blinking cursor never reaches exact equality, so 0 burns the full
    stabilize_max_wait_sec budget on every such step, quietly."""
    from monkey_collector.config import CollectionConfig, RunConfig, _validate

    cfg = RunConfig(collection=CollectionConfig(stabilize_pixel_threshold=0.0))
    _validate(cfg)  # does not raise


@pytest.mark.parametrize("value", [-1, 256, 10.5, True])
def test_stabilize_luma_delta_out_of_range_is_rejected(tmp_path, value):
    path = write_yaml(tmp_path, f"collection:\n  stabilize_luma_delta: {value}\n")
    with pytest.raises(ConfigError, match="stabilize_luma_delta"):
        load_run_config(path)


@pytest.mark.parametrize("value", [0, -1])
def test_nonpositive_stabilize_low_res_width_is_rejected(tmp_path, value):
    path = write_yaml(tmp_path, f"collection:\n  stabilize_low_res_width: {value}\n")
    with pytest.raises(ConfigError, match="stabilize_low_res_width"):
        load_run_config(path)


def test_negative_stabilize_poll_ms_is_rejected(tmp_path):
    path = write_yaml(tmp_path, "collection:\n  stabilize_poll_ms: -1\n")
    with pytest.raises(ConfigError, match="stabilize_poll_ms"):
        load_run_config(path)


def test_zero_stabilize_poll_ms_is_accepted(tmp_path):
    path = write_yaml(tmp_path, "collection:\n  stabilize_poll_ms: 0\n")
    assert load_run_config(path).collection.stabilize_poll_ms == 0


# ---------------------------------------------------------------------------
# llm.input_mode / collection.budget_mode
# ---------------------------------------------------------------------------


def test_bad_budget_mode_and_input_mode_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="budget_mode"):
        load_run_config(write_yaml(tmp_path, "collection:\n  budget_mode: forever\n"))
    with pytest.raises(ConfigError, match="input_mode"):
        load_run_config(write_yaml(tmp_path, "llm:\n  input_mode: telepathy\n", "b.yaml"))


# ---------------------------------------------------------------------------
# export — Stage-1 knobs
# ---------------------------------------------------------------------------


def test_export_ratios_must_be_fractions(tmp_path):
    with pytest.raises(ConfigError, match="export.ood_apps"):
        load_run_config(write_yaml(tmp_path, "export:\n  ood_apps: 1.0\n"))
    with pytest.raises(ConfigError, match="export.id_ratio"):
        load_run_config(write_yaml(tmp_path, "export:\n  id_ratio: 1.0\n", "b.yaml"))


def test_export_ratio_of_zero_is_accepted(tmp_path):
    path = write_yaml(tmp_path, "export:\n  ood_apps: 0.0\n")
    assert load_run_config(path).export.ood_apps == 0.0


def test_target_size_accepts_string_form_via_env(monkeypatch):
    monkeypatch.setenv("MC_EXPORT_TARGET_SIZE", "840x1876")
    assert load_run_config().export.target_size == (840, 1876)


def test_config_error_is_a_valueerror(tmp_path):
    """Callers written against `except ValueError` keep working."""
    with pytest.raises(ValueError, match="device.width"):
        load_run_config(write_yaml(tmp_path, "device:\n  width: 0\n"))


# ---------------------------------------------------------------------------
# Time budget — the default session end condition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("2h", 7200),
        ("120m", 7200),
        ("7200s", 7200),
        ("7200", 7200),
        (7200, 7200),
        (7200.0, 7200),
        ("1.5h", 5400),
        ("90m", 5400),
        ("30S", 30),  # suffix is case-insensitive
        (" 2h ", 7200),  # surrounding whitespace tolerated
    ],
)
def test_parse_duration_accepts_the_documented_grammar(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize(
    "bad",
    ["2 hours", "abc", "", "h", "0", "0s", "-5h", -1, 0, None, True, False],
)
def test_parse_duration_raises_instead_of_falling_back(bad):
    """A typo'd budget must NOT silently become 2h — same failure class as a
    typo'd YAML section: the run looks perfectly configured while doing
    something the operator never asked for."""
    with pytest.raises(ConfigError):
        parse_duration(bad)


def test_a_malformed_duration_fails_the_whole_config_load(tmp_path):
    path = write_yaml(tmp_path, 'collection:\n  max_duration: "2 hours"\n')
    with pytest.raises(ConfigError, match="max_duration"):
        load_run_config(path)


def test_the_inactive_budget_is_validated_too(tmp_path):
    """A bad max_duration is rejected even while budget_mode is `steps`."""
    path = write_yaml(
        tmp_path, 'collection:\n  budget_mode: steps\n  max_duration: "nope"\n'
    )
    with pytest.raises(ConfigError, match="max_duration"):
        load_run_config(path)

    path = write_yaml(tmp_path, "collection:\n  budget_mode: time\n  max_steps: 0\n", "b.yaml")
    with pytest.raises(ConfigError, match="max_steps"):
        load_run_config(path)


def test_max_duration_can_be_overridden_by_file_and_env(tmp_path, monkeypatch):
    path = write_yaml(tmp_path, 'collection:\n  max_duration: "45m"\n')
    assert load_run_config(path).collection.max_duration_sec == 2700

    monkeypatch.setenv("MC_COLLECTION_MAX_DURATION", "90m")
    assert load_run_config(path).collection.max_duration_sec == 5400
