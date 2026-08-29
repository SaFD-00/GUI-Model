"""Monkey-Collector 중앙 설정 로딩: YAML → dataclass, 환경변수 오버라이드.

Resolution order (later wins):
  builtin defaults (this file) → config/run.yaml → MC_* env vars → CLI flags

CLI flags are read directly off ``args`` at each subcommand's call site (e.g.
``args.serial or args.run_config.device.serial`` in ``cli.py``) — there is no
generic CLI-merge layer here, because no subcommand wired so far
(``catalog``/``sync-installed``/``provision``/``reset``) needs one; the future
``run`` subcommand can add one when it lands.

The builtin defaults must match ``config/run.yaml`` exactly. A **missing**
``run.yaml`` is not an error — the builtin defaults stand, so
``monkey-collect --help`` and unit tests never depend on the file being
present.

Env override naming is mechanical: ``MC_{SECTION}_{KEY}`` upper-cased, e.g.
``MC_DEVICE_SERIAL``, ``MC_COLLECTION_MAX_STEPS``,
``MC_PAGE_MATCHING_MAX_DIFF_ELEMENTS``. ``export.target_size`` is the one
non-scalar and takes the ``"840x1876"`` form.

**Every** way this module can refuse a config raises :class:`ConfigError` —
missing explicit path, unparseable YAML, a non-mapping root, an unknown SECTION,
an unknown KEY, or an out-of-range value. ``cli.py`` catches exactly that one
type and exits 2. A silently-ignored setting is the failure mode this module is
built to prevent: a misconfigured run that *looks* successful is worse than one
that never starts.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from monkey_collector.paths import config_path

# ---------------------------------------------------------------------------
# Builtin defaults — must match config/run.yaml canonical values exactly.
# ---------------------------------------------------------------------------

_BUILTIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "device": {
        # Pinned to the real Pixel 6, NOT null. This host also has
        # emulator-5556 attached for unrelated work; a null default would let
        # autodetection pick either device (or die on ambiguity), and picking
        # the emulator by accident is the one outcome worth hard-coding
        # against. See config/run.yaml for the full rationale.
        "serial": "19101FDF6004EH",
        "width": 1080,
        "height": 2400,
    },
    "collection": {
        "budget_mode": "time",
        "max_duration": "2h",
        "max_steps": 1500,
        "seed": 42,
        "action_delay_ms": 1500,
        # host-pull only: the device never signals "screen changed", so the host
        # polls screenshots until the picture stops moving, then dumps the
        # hierarchy exactly once. See monkey_collector.stabilize for the
        # measurements behind these defaults.
        "stabilize_poll_ms": 0,
        "stabilize_max_wait_sec": 8.0,
        "stabilize_pixel_threshold": 0.005,
        "stabilize_luma_delta": 10,
        "stabilize_low_res_width": 100,
    },
    "page_matching": {
        "merge_policy": "similar_elements",
        "max_diff_elements": 2,
        "same_activity_only": True,
    },
    "llm": {
        "model": "qwen/qwen3.8-flash",
        "input_mode": "api",
    },
    "export": {
        "target_size": [840, 1876],
        "ood_apps": 0.3,
        "id_ratio": 0.1,
    },
}

VALID_BUDGET_MODES: frozenset[str] = frozenset({"steps", "time"})
VALID_MERGE_POLICIES: frozenset[str] = frozenset({"structure_only", "similar_elements"})
VALID_INPUT_MODES: frozenset[str] = frozenset({"api", "random"})

ENV_PREFIX = "MC"


class ConfigError(ValueError):
    """Raised for every rejected configuration, whatever the reason.

    Subclasses :class:`ValueError` so that ``except ValueError`` around a
    ``load_run_config()`` call keeps working; ``cli.py`` catches this precise
    type to turn a bad config into a clean exit-2 instead of a traceback.
    """


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DeviceConfig:
    """Target device. ``serial=None`` means autodetect from ``adb devices``.

    Defaults to the real Pixel 6's serial rather than ``None`` — see the
    ``_BUILTIN_DEFAULTS["device"]`` comment for why.
    """

    serial: str | None = "19101FDF6004EH"
    width: int = 1080
    height: int = 2400


@dataclass
class CollectionConfig:
    """Collection loop budget and host-pull stabilization knobs."""

    #: Which budget ends a session. ``time`` (default) runs until
    #: :attr:`max_duration_sec` of wall clock elapses; ``steps`` runs until
    #: :attr:`max_steps` actions have been performed.
    budget_mode: str = "time"  # steps | time
    #: Wall-clock budget per app, as a duration string ("2h" / "120m" / "7200s")
    #: or a bare number of seconds. Only consulted when ``budget_mode == "time"``.
    max_duration: str = "2h"
    #: Action budget per app. Only consulted when ``budget_mode == "steps"``.
    max_steps: int = 1500
    seed: int = 42
    action_delay_ms: int = 1500
    #: Extra sleep between screenshot polls. Zero by default.
    stabilize_poll_ms: int = 0
    #: Give up waiting after this long and accept the last frame as-is.
    stabilize_max_wait_sec: float = 8.0
    #: Changed-pixel fraction at or below which a screen counts as settled.
    #: NOT zero: a blinking cursor, video or spinner never reaches equality, and
    #: under strict equality every step on such a screen burns the full wait —
    #: quietly, since data keeps coming out even as the run slows to a crawl.
    stabilize_pixel_threshold: float = 0.005
    #: Per-pixel intensity delta (0-255) that counts as a changed pixel.
    stabilize_luma_delta: int = 10
    #: Width the comparison thumbnail is downscaled to.
    stabilize_low_res_width: int = 100

    @property
    def max_duration_sec(self) -> int:
        """:attr:`max_duration` in whole seconds. Validated at load time."""
        return parse_duration(self.max_duration)


@dataclass
class PageMatchingConfig:
    """Page-identity knobs, ported from LLM-Explorer. LLM-free by construction.

    Pixels are NOT part of page identity — screenshot comparison belongs to
    screen STABILIZATION (:mod:`monkey_collector.stabilize`) and nothing else.
    """

    #: ``similar_elements`` (default) or ``structure_only``.
    merge_policy: str = "similar_elements"
    #: Symmetric-difference budget. Measured margin on the Pixel 6: same page
    #: scrolled = 2, nearest genuinely different screen = 18.
    max_diff_elements: int = 2
    #: Refuse to merge across foreground activities.
    same_activity_only: bool = True


@dataclass
class LlmConfig:
    """LLM settings. The LLM is used for input-text generation ONLY."""

    model: str = "qwen/qwen3.8-flash"
    input_mode: str = "api"  # api | random


@dataclass
class ExportConfig:
    """Stage-1 export. ``target_size`` is part of the EXP08 export contract.

    ``ood_apps`` and ``id_ratio`` are two INDEPENDENT knobs:
      - ``ood_apps``: fraction of APPS held out entirely from train (OOD eval)
      - ``id_ratio``: fraction of the SEEN apps' triples reserved as ID eval
    """

    target_size: tuple[int, int] = (840, 1876)
    ood_apps: float = 0.3
    id_ratio: float = 0.1

    @property
    def target_width(self) -> int:
        return self.target_size[0]

    @property
    def target_height(self) -> int:
        return self.target_size[1]


@dataclass
class RunConfig:
    """Fully resolved configuration for one Monkey-Collector invocation."""

    device: DeviceConfig = field(default_factory=DeviceConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    page_matching: PageMatchingConfig = field(default_factory=PageMatchingConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    #: The run.yaml actually read, or None when builtin defaults stood alone.
    source_path: Path | None = None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge *overlay* into a copy of *base* (overlay wins)."""
    out = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _coerce(raw: str, default: Any) -> Any:
    """Coerce an env-var string to the type of the builtin default."""
    if isinstance(default, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw


def _parse_target_size(value: Any) -> tuple[int, int]:
    """Accept ``[840, 1876]``, ``(840, 1876)`` or ``"840x1876"``."""
    parts = value.lower().replace(",", "x").split("x") if isinstance(value, str) else list(value)
    if len(parts) != 2:
        raise ConfigError(f"export.target_size must have exactly 2 ints, got: {value!r}")
    return (int(parts[0]), int(parts[1]))


def parse_duration(value: Any) -> int:
    """Parse a wall-clock duration into whole seconds.

    Accepts an int/float (already seconds), or a string with an optional
    h/m/s suffix (case-insensitive): "2h" -> 7200, "120m" -> 7200,
    "7200s"/"7200" -> 7200.

    RAISES on anything unparsable or non-positive rather than falling back to
    a default. A typo'd budget that silently becomes "2h" is the same failure
    class as a typo'd YAML section — the run looks perfectly configured while
    doing something the operator never asked for.
    """
    if isinstance(value, bool) or value is None:
        raise ConfigError(f"collection.max_duration must be a duration, got {value!r}")
    if isinstance(value, (int, float)):
        seconds = int(value)
    else:
        text = str(value).strip().lower()
        multiplier = 1
        if text.endswith("h"):
            multiplier, text = 3600, text[:-1]
        elif text.endswith("m"):
            multiplier, text = 60, text[:-1]
        elif text.endswith("s"):
            multiplier, text = 1, text[:-1]
        try:
            seconds = int(float(text) * multiplier)
        except ValueError:
            raise ConfigError(
                f"collection.max_duration is not a duration: {value!r}. "
                f'Use "2h", "120m", "7200s", or a bare number of seconds.'
            ) from None
    if seconds <= 0:
        raise ConfigError(
            f"collection.max_duration must be positive, got {value!r} ({seconds}s)"
        )
    return seconds


def _apply_env_overrides(data: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Overlay ``MC_{SECTION}_{KEY}`` env vars onto *data* in place-ish."""
    out = copy.deepcopy(data)
    for section, defaults in _BUILTIN_DEFAULTS.items():
        for key, default in defaults.items():
            env_name = f"{ENV_PREFIX}_{section}_{key}".upper()
            raw = os.environ.get(env_name)
            if raw is None:
                continue
            if key == "target_size":
                out[section][key] = _parse_target_size(raw)
            else:
                out[section][key] = _coerce(raw, default)
    return out


def _require_positive_int(name: str, value: Any) -> None:
    """Reject 0, negatives and non-ints for a pixel dimension / count.

    ``device.width`` / ``device.height`` are the SOURCE FRAME of every coordinate
    transform. Unvalidated, ``0`` reaches the parser as a raw ``ZeroDivisionError``
    from deep inside a resize, and ``-5`` is worse still: it never raises at all —
    the resize happily returns negative dims and every exported box comes out
    negative-scaled. Both must die here, at the boundary.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(
            f"{name} must be a positive int, got {value!r}."
        )


def _require_unit_interval(name: str, value: Any) -> None:
    """Reject anything outside the closed interval [0.0, 1.0] for a ratio/threshold."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number in [0.0, 1.0], got {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ConfigError(f"{name} must be in [0.0, 1.0], got {value!r}")


def _validate(cfg: RunConfig) -> None:
    """Range/enum checks over the fully resolved config. Raises :class:`ConfigError`."""
    # -- device: the coordinate source frame ---------------------------------
    _require_positive_int("device.width", cfg.device.width)
    _require_positive_int("device.height", cfg.device.height)

    # -- collection ----------------------------------------------------------
    if cfg.collection.budget_mode not in VALID_BUDGET_MODES:
        raise ConfigError(
            f"collection.budget_mode must be one of {sorted(VALID_BUDGET_MODES)}, "
            f"got {cfg.collection.budget_mode!r}"
        )
    # Validate BOTH budgets regardless of the active mode: the inactive one is
    # still committed config, and finding out it is malformed only after someone
    # flips budget_mode is the kind of delayed failure this module exists to avoid.
    parse_duration(cfg.collection.max_duration)
    _require_positive_int("collection.max_steps", cfg.collection.max_steps)

    # -- collection: host-pull screen stabilization --------------------------
    if cfg.collection.stabilize_max_wait_sec <= 0:
        raise ConfigError(
            "collection.stabilize_max_wait_sec must be positive, got "
            f"{cfg.collection.stabilize_max_wait_sec!r} — it is the backstop that keeps a "
            "never-settling screen (video, spinner, blinking cursor) from stalling the run"
        )
    _require_unit_interval(
        "collection.stabilize_pixel_threshold", cfg.collection.stabilize_pixel_threshold
    )
    luma = cfg.collection.stabilize_luma_delta
    if isinstance(luma, bool) or not isinstance(luma, int) or not 0 <= luma <= 255:
        raise ConfigError(
            f"collection.stabilize_luma_delta must be an int in [0, 255] "
            f"(per-pixel intensity delta), got {luma!r}"
        )
    _require_positive_int(
        "collection.stabilize_low_res_width", cfg.collection.stabilize_low_res_width
    )
    if cfg.collection.stabilize_poll_ms < 0:
        raise ConfigError(
            f"collection.stabilize_poll_ms must be >= 0, got {cfg.collection.stabilize_poll_ms!r}"
        )

    # -- page_matching: LLM-free page identity ------------------------------
    policy = cfg.page_matching.merge_policy
    if policy not in VALID_MERGE_POLICIES:
        raise ConfigError(
            f"page_matching.merge_policy must be one of {sorted(VALID_MERGE_POLICIES)}, "
            f"got {policy!r}"
        )
    budget = cfg.page_matching.max_diff_elements
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise ConfigError(
            f"page_matching.max_diff_elements must be a non-negative int "
            f"(content-free signature symmetric-difference budget), got {budget!r}"
        )

    # -- llm -----------------------------------------------------------------
    if cfg.llm.input_mode not in VALID_INPUT_MODES:
        raise ConfigError(
            f"llm.input_mode must be one of {sorted(VALID_INPUT_MODES)}, "
            f"got {cfg.llm.input_mode!r}"
        )

    # -- export --------------------------------------------------------------
    for name, value in (("ood_apps", cfg.export.ood_apps), ("id_ratio", cfg.export.id_ratio)):
        if not 0.0 <= value < 1.0:
            raise ConfigError(f"export.{name} must be in [0.0, 1.0), got {value}")


def _read_yaml(yaml_path: Path) -> dict[str, Any]:
    """Read *yaml_path* into a section mapping, rejecting every malformed shape.

    An empty file is legal (``{}``). A non-mapping root, an unknown SECTION and a
    non-mapping section body are not — the first two would crash far from the
    cause, and an unknown section is the silent failure this whole function
    exists to stop: ``collectoin: {max_steps: 7}`` would otherwise load cleanly,
    leaving ``max_steps`` at 1500 while the run looked perfectly configured.
    """
    try:
        with yaml_path.open(encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file is not valid YAML: {yaml_path}\n  {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Config file could not be read: {yaml_path}\n  {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"Config file must hold a YAML mapping of sections at the top level: {yaml_path}\n"
            f"  got: {type(loaded).__name__}"
        )

    unknown = sorted(set(loaded) - set(_BUILTIN_DEFAULTS))
    if unknown:
        raise ConfigError(
            f"Unknown config section(s) in {yaml_path}: {', '.join(unknown)}\n"
            f"  valid sections: {', '.join(sorted(_BUILTIN_DEFAULTS))}\n"
            f"  A typo'd section would otherwise be ignored in silence and the run "
            f"would proceed on defaults while looking configured."
        )

    out: dict[str, Any] = {}
    for section, body in loaded.items():
        if body is None:  # `device:` with nothing under it — an empty override
            out[section] = {}
        elif isinstance(body, dict):
            out[section] = body
        else:
            raise ConfigError(
                f"Config section {section!r} in {yaml_path} must be a mapping of keys, "
                f"got {type(body).__name__}"
            )
    return out


def load_run_config(path: str | Path | None = None) -> RunConfig:
    """Resolve builtin defaults → ``config/run.yaml`` → ``MC_*`` env vars.

    Args:
        path: explicit YAML path. Defaults to ``config/run.yaml`` under the
            project root. A missing default file is fine (builtin defaults
            stand); a missing *explicit* path is an error, since the caller
            asked for it by name.

    Raises:
        ConfigError: for every rejected config — missing explicit path,
            unparseable YAML, non-mapping root, unknown section, unknown key,
            or out-of-range value.
    """
    explicit = path is not None
    yaml_path = config_path() if path is None else Path(path)

    raw: dict[str, Any] = {}
    source: Path | None = None
    if yaml_path.exists():
        raw = _read_yaml(yaml_path)
        source = yaml_path
    elif explicit:
        raise ConfigError(
            f"Config file not found: {yaml_path}\n"
            f"  --config names the file explicitly, so a missing one is a typo, not a default."
        )

    merged = _deep_merge(_BUILTIN_DEFAULTS, raw)
    merged = _apply_env_overrides(merged)

    try:
        export_raw = dict(merged["export"])
        export_raw["target_size"] = _parse_target_size(export_raw["target_size"])
        device_raw = dict(merged["device"])
        # An empty-string serial (e.g. `MC_DEVICE_SERIAL=` left set in a shell)
        # must not silently become a "pinned" empty serial — that would still
        # bypass autodetection while addressing no device at all. Treat it the
        # same as unset -> autodetect.
        if device_raw.get("serial") == "":
            device_raw["serial"] = None
        cfg = RunConfig(
            device=DeviceConfig(**device_raw),
            collection=CollectionConfig(**merged["collection"]),
            page_matching=PageMatchingConfig(**merged["page_matching"]),
            llm=LlmConfig(**merged["llm"]),
            export=ExportConfig(**export_raw),
            source_path=source,
        )
    except (TypeError, ValueError) as exc:
        # Unknown KEY inside a known section (dataclass TypeError), or a
        # malformed export.target_size. Name the file so the typo is findable.
        where = f" in {yaml_path}" if source is not None else ""
        raise ConfigError(f"Invalid config{where}: {exc}") from exc

    _validate(cfg)
    return cfg


__all__ = [
    "ENV_PREFIX",
    "VALID_BUDGET_MODES",
    "VALID_INPUT_MODES",
    "VALID_MERGE_POLICIES",
    "CollectionConfig",
    "ConfigError",
    "DeviceConfig",
    "ExportConfig",
    "LlmConfig",
    "PageMatchingConfig",
    "RunConfig",
    "load_run_config",
    "parse_duration",
]
