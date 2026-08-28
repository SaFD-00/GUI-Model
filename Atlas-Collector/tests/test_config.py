"""Config loading and validation.

``config.py`` shipped with zero tests, and three silent-failure modes came out of
that: a device dimension of ``0`` reached the parser as a raw ``ZeroDivisionError``,
``-5`` produced negative-scaled boxes without any error at all, and a typo'd YAML
SECTION (``collectoin:``) loaded cleanly while leaving every value at its default —
a misconfigured run that looked perfectly successful.

Every rejection path is asserted to raise :class:`ConfigError` and to NAME the
offending setting, because the whole point is that the operator finds the typo.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from atlas_collector.config import ConfigError, load_run_config

# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_ac_env(monkeypatch):
    """Strip ``AC_*`` overrides.

    ``_apply_env_overrides`` reads the live environment, so a stray
    ``AC_COLLECTION_MAX_STEPS`` in the developer's shell would otherwise make
    every builtin-defaults assertion here flaky.
    """
    for key in list(os.environ):
        if key.startswith("AC_"):
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
        "atlas_collector.config.config_path", lambda *a, **k: tmp_path / "absent.yaml"
    )
    cfg = load_run_config()
    assert cfg.source_path is None
    assert cfg.collection.max_steps == 1500
    assert cfg.device.width == 1080
    assert cfg.device.height == 2400
    assert cfg.export.target_size == (840, 1876)


def test_committed_run_yaml_loads_and_matches_the_builtin_defaults():
    """The real ``config/run.yaml`` must load, and must not drift from the builtins."""
    cfg = load_run_config()
    assert cfg.source_path is not None
    assert cfg.source_path.name == "run.yaml"
    assert cfg.collection.max_steps == 1500
    assert cfg.device.width == 1080
    assert cfg.export.target_size == (840, 1876)
    assert cfg.llm.model == "qwen/qwen3.8-flash"


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
    monkeypatch.setenv("AC_COLLECTION_MAX_STEPS", "11")
    assert load_run_config(path).collection.max_steps == 11


# ---------------------------------------------------------------------------
# (3a) device.width / device.height — the coordinate SOURCE FRAME
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, -5, -1080])
@pytest.mark.parametrize("key", ["width", "height"])
def test_nonpositive_device_dimension_is_rejected(tmp_path, key, value):
    """0 used to detonate as ZeroDivisionError inside the parser; -5 never raised at all.

    ``smart_resize_dims(2400, -1080)`` returns ``(2408, -1092)`` quite happily, so a
    negative dimension produced negative-scaled boxes with no error anywhere. Both
    must be refused at the config boundary instead.
    """
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
# (3b) unknown top-level SECTIONS are rejected like unknown keys
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
# (3c) screen_matching thresholds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("page_pixel_diff_threshold", 1.5),
        ("page_pixel_diff_threshold", -0.1),
        ("page_pixel_diff_threshold", 42),
        ("element_jaccard_min", 2.0),
        ("element_jaccard_min", -1.0),
        ("element_diff_max", -1),
        ("element_diff_max", 0.5),
    ],
)
def test_out_of_range_screen_matching_value_is_rejected(tmp_path, key, value):
    path = write_yaml(tmp_path, f"screen_matching:\n  {key}: {value}\n")
    with pytest.raises(ConfigError, match=f"screen_matching.{key}"):
        load_run_config(path)


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_threshold_boundaries_are_accepted(tmp_path, value):
    """[0.0, 1.0] inclusive: the endpoints are degenerate settings, not crashes."""
    path = write_yaml(tmp_path, f"screen_matching:\n  page_pixel_diff_threshold: {value}\n")
    assert load_run_config(path).screen_matching.page_pixel_diff_threshold == value


def test_element_diff_max_zero_is_accepted(tmp_path):
    path = write_yaml(tmp_path, "screen_matching:\n  element_diff_max: 0\n")
    assert load_run_config(path).screen_matching.element_diff_max == 0


# ---------------------------------------------------------------------------
# Pre-existing enum / range checks still hold
# ---------------------------------------------------------------------------


def test_bad_budget_mode_and_input_mode_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="budget_mode"):
        load_run_config(write_yaml(tmp_path, "collection:\n  budget_mode: forever\n"))
    with pytest.raises(ConfigError, match="input_mode"):
        load_run_config(write_yaml(tmp_path, "llm:\n  input_mode: telepathy\n", "b.yaml"))


def test_export_ratios_must_be_fractions(tmp_path):
    with pytest.raises(ConfigError, match="export.ood_apps"):
        load_run_config(write_yaml(tmp_path, "export:\n  ood_apps: 1.0\n"))


def test_config_error_is_a_valueerror(tmp_path):
    """Callers written against the old `except ValueError` keep working."""
    with pytest.raises(ValueError, match="device.width"):
        load_run_config(write_yaml(tmp_path, "device:\n  width: 0\n"))
