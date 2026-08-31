"""CLI entrypoint for monkey-collector (``monkey-collect``).

Host-pull rebuild: the foundation (``adb.py``, ``paths.py``, ``xml/``,
``pagematch.py``, ``stabilize.py``, ``session.py``, ``catalog.py``,
``provision.py``), the collection loop (``loop.py``, ``explore.py``, ``aig.py``,
``semantic.py``) and the Stage-1 export (``export.py``) are done. Six
subcommands are wired here because their implementations exist: ``catalog``,
``sync-installed``, ``provision``, ``reset``, ``run`` and ``export``.

Every subcommand here is backed by a working implementation; a subcommand that
only ever raises ``NotImplementedError`` would make ``--help`` describe
capabilities the tool does not have, so none is ever registered ahead of its
code. ``export`` (the Stage-1 jsonl export) joined them with M5 and is wired to
:mod:`monkey_collector.export`.

``main()`` resolves the run config BEFORE dispatching, for every subcommand
including ``catalog``. That is deliberate: ``--config`` naming a missing or
unparseable file must fail loudly here rather than be discovered later. The
resolved :class:`~monkey_collector.config.RunConfig` is attached to the parsed
namespace as ``args.run_config`` so a subcommand can consume it without
re-reading the file.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from monkey_collector.paths import (
    DEFAULT_ROOT,
    collection_root,
    export_root,
    raw_root,
    review_root,
    runtime_root,
)

#: An app contributing fewer records than this is called out by `export`. Not a
#: threshold on quality: a number this small usually means the collection got
#: stuck, and the reviewer's filter made that visible rather than causing it.
THIN_APP_RECORDS = 25

if TYPE_CHECKING:  # imported for typing only; the runtime imports stay local
    from monkey_collector.adb import AdbClient
    from monkey_collector.catalog import AppRow

# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def _fmt_table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows
    ]
    return "\n".join([line, sep, *body])


def cmd_catalog(args: argparse.Namespace) -> int:
    """List / summarise ``catalog/apps.csv``."""
    from monkey_collector.catalog import catalog_stats, filter_rows, load_catalog

    rows = load_catalog(args.catalog)
    installed = None if args.installed is None else args.installed == "true"
    selected = filter_rows(
        rows,
        tier=args.tier,
        installed=installed,
        auth=args.auth,
        status=args.status,
        include_auth=not args.exclude_auth,
    )

    if args.stats:
        stats = catalog_stats(selected)
        print(f"catalog: {len(rows)} rows total, {stats['total']} selected")
        print(f"  installed      : {stats['installed']} (not installed: {stats['not_installed']})")
        print(f"  device_verified: {stats['device_verified']}")
        print(
            f"  collectable    : {stats['collectable']}"
            "  (installed AND package resolved AND not excluded)"
        )
        print(f"  excluded       : {stats['excluded']}  (status == excluded; never collected)")
        print(f"  pending        : {stats['pending']}  (package_id == PENDING)")
        for label in ("tier", "auth_required", "status", "category"):
            entries = stats[label]
            assert isinstance(entries, dict)
            rendered = ", ".join(f"{k}={v}" for k, v in sorted(entries.items()))
            print(f"  {label:<15}: {rendered}")
        return 0

    if not selected:
        print("No catalog rows match the given filters.", file=sys.stderr)
        return 1

    headers = ["tier", "package_id", "app_name", "auth_required", "installed", "status"]
    table = [
        [r.tier, r.package_id, r.app_name, r.auth_required, r.installed, r.status]
        for r in selected
    ]
    print(_fmt_table(table, headers))
    print(f"\n{len(selected)} / {len(rows)} rows")
    return 0


# ---------------------------------------------------------------------------
# sync-installed / provision — device-facing catalog maintenance
# ---------------------------------------------------------------------------


def _device_packages(args: argparse.Namespace, command: str) -> tuple[AdbClient, set[str] | None]:
    """Open the device and read ``pm list packages``.

    Returns ``(client, packages)``; *packages* is None when adb failed, which the
    caller turns into a non-zero exit. Both commands start here because both of
    them treat the device — not the catalog — as the authority on what is
    installed, and neither has anything meaningful to do without it.
    """
    from monkey_collector.adb import AdbClient, AdbError

    client = AdbClient(serial=args.serial or args.run_config.device.serial)
    try:
        return client, set(client.list_packages())
    except AdbError as exc:
        print(f"monkey-collect {command}: {exc}", file=sys.stderr)
        return client, None


def cmd_sync_installed(args: argparse.Namespace) -> int:
    """Refresh the catalog's ``installed`` column from the device.

    That column and no other: ``status`` is the curation verdict and survives
    every sync untouched. See ``provision.sync_installed_rows`` and the
    ``catalog`` module docstring for why conflating the two loses data silently.
    """
    from monkey_collector.catalog import load_catalog, write_catalog
    from monkey_collector.provision import sync_installed_rows

    rows = load_catalog(args.catalog)
    client, device_packages = _device_packages(args, "sync-installed")
    if device_packages is None:
        return 1

    updated, flips = sync_installed_rows(rows, device_packages)
    before = sum(1 for r in rows if r.is_installed)
    after = sum(1 for r in updated if r.is_installed)
    print(f"device {client.serial}: {len(device_packages)} packages present")
    print(f"catalog: {len(rows)} rows — installed {before} -> {after}")
    if flips:
        gained = sum(1 for f in flips if f.after == "true")
        lost = len(flips) - gained
        print(f"  {len(flips)} row(s) flipped ({gained} false->true, {lost} true->false)")
        for flip in flips:
            print(f"    {flip.direction:<14} {flip.package_id}  ({flip.app_name})")
    else:
        print("  0 rows flipped — the catalog already matches the device")
    print("  status column: untouched (curation verdict, not a device fact)")

    if args.dry_run:
        print("dry-run: catalog NOT written")
        return 0
    print(f"wrote {write_catalog(updated, args.catalog)}")
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    """Resolve and install the APKs a collection run needs."""
    from monkey_collector import provision as prov
    from monkey_collector.catalog import load_catalog

    rows = load_catalog(args.catalog)
    client, device_packages = _device_packages(args, "provision")
    if device_packages is None:
        return 1

    ledger_file = Path(args.ledger) if args.ledger else prov.ledger_path()
    # Loaded BEFORE anything is installed: a corrupt ledger discovered afterwards
    # would mean a run whose outcome cannot be recorded anywhere.
    try:
        ledger = prov.load_ledger(ledger_file)
    except prov.LedgerError as exc:
        print(f"monkey-collect provision: {exc}", file=sys.stderr)
        return 1

    plan = prov.plan_provision(rows, device_packages, only=args.only, force=args.force)
    for package_id in plan.unknown:
        print(f"  ?       {package_id}: no such row in the catalog", file=sys.stderr)
    for row, reason in plan.skipped:
        print(f"  skip    {row.package_id}  ({reason})")
    print(f"{len(plan.targets)} package(s) to provision")

    cache_dir = Path(args.apk_cache) if args.apk_cache else prov.apk_cache_dir()
    aw_dir = Path(args.android_world) if args.android_world else prov.android_world_dir()

    failures: dict[str, tuple[str, str]] = {}
    # Already-installed skips count as resolved: they clear any stale ledger
    # entry, because the ledger records "not held", not "last attempt failed".
    present: set[str] = {r.package_id for r, why in plan.skipped if why == prov.SKIP_INSTALLED}
    for row in plan.targets:
        resolution = prov.resolve_apk(
            row.package_id,
            cache_dir=cache_dir,
            aw_dir=aw_dir,
            dest_dir=prov.download_dir(),
            allow_download=not args.no_download,
        )
        if resolution.path is None:
            print(f"  MISSING {row.package_id}: {resolution.reason}")
            failures[row.package_id] = ("unresolved", resolution.reason)
            continue
        label = f"{resolution.source}:{resolution.path.name}"
        if args.dry_run:
            print(f"  plan    {row.package_id}  <- {label}")
            continue
        # `already_present` is only ever True on the --force path. It tells
        # install_apk that a post-install re-query cannot prove anything here,
        # because the package was on the device before the install even ran.
        ok, note = prov.install_apk(
            client,
            row.package_id,
            resolution.path,
            already_present=row.package_id in device_packages,
        )
        if ok:
            present.add(row.package_id)
            print(f"  OK      {row.package_id}  <- {label}" + (f"  [{note}]" if note else ""))
        else:
            failures[row.package_id] = (resolution.source, note)
            print(f"  FAILED  {row.package_id}  <- {label}: {note}")

    if args.dry_run:
        print(f"dry-run: nothing installed, {ledger_file.name} not written")
        return 1 if failures else 0

    written = prov.write_ledger(
        prov.update_ledger(
            ledger, failures=failures, resolved=present, scope_ids=plan.scope_ids
        ),
        ledger_file,
    )
    print(f"{len(present)} present, {len(failures)} failed — ledger: {written}")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# run — the host-pull collection loop
# ---------------------------------------------------------------------------

#: Device preconditions applied before a sweep, as (label, shell command).
#: Both are documented in AGENTS §1 with the pilot they came from:
#:
#: * the screen locking makes every dump `com.android.systemui`, and the lock
#:   survives nothing but a swipe -- `stayon` is the only thing that prevents it.
#:   It is cleared by unplugging USB, so it is re-applied every run.
#: * the GMS "System update needed" modal makes every dump
#:   `com.google.android.gms`, and its ONLY button is "Download & install now".
#:   This collector has no action guard by design (AGENTS §0.5), so an
#:   unsuppressed modal is an explorer one tap away from a 1.35 GB download and
#:   a reboot. Suppressing it is the one place that decision needed a fence.
#: * the rotation lock came from the first real-device pilot: something during
#:   exploration set `user_rotation=1`, the display went to 2400x1080, and it
#:   STAYED there -- 23 of 77 observations were landscape and the setting
#:   persisted after the run, so the next run would have started rotated.
#:
#:   What this buys and what it does not: it stops INCIDENTAL rotation. It does
#:   NOT override an app that declares `screenOrientation="landscape"` (VLC,
#:   YouTube, Open Camera and Retro Music are all collection targets and will
#:   still rotate). Making a rotated screen export CORRECTLY is the export's
#:   job, not this lock's -- see `export.frame_for_observation`.
#:
#: Nothing here is restored afterwards, and that is deliberate: `stayon` and the
#: OTA suppression already outlive the run, and a sweep that dies mid-flight is
#: better off leaving the device portrait-locked than landscape.
DEVICE_PREPARATION: tuple[tuple[str, str], ...] = (
    ("keep the screen on", "svc power stayon true"),
    ("suppress OTA prompts", "settings put global ota_disable_automatic_update 1"),
    ("disable auto-rotate", "settings put system accelerometer_rotation 0"),
    ("lock the display to portrait", "settings put system user_rotation 0"),
)

#: Read, never written: the first thing to look at when a session reports
#: "stuck outside the app", which is far more often the device than the code.
FOCUS_PROBE = "dumpsys window | grep mCurrentFocus"


def prepare_device(adb: AdbClient) -> list[str]:
    """Apply AGENTS §1's preconditions and probe the focused window.

    Returns the log lines produced, so a caller (and a test) can see exactly
    what was applied. Each command is independent: `grep` exits non-zero when it
    matches nothing and :meth:`AdbClient.shell` turns that into an error, which
    must not stop the other two from running.
    """
    lines: list[str] = []
    for label, command in DEVICE_PREPARATION:
        try:
            adb.shell(command)
            lines.append(f"  device: {label} ({command})")
        except Exception as error:  # noqa: BLE001 - one refusal must not stop the sweep
            lines.append(f"  device: {label} FAILED ({error})")
    try:
        focus = adb.shell(FOCUS_PROBE).strip().splitlines()
    except Exception as error:  # noqa: BLE001 - a probe is never fatal
        focus = [f"unavailable ({error})"]
    lines.append(f"  device: focused window = {focus[0] if focus else '(none)'}")
    for line in lines:
        print(line)
        # Also into `{root}/run.log`: what the device was doing at the start is
        # the first thing to look at when a session reports "stuck outside the
        # app", and stdout is gone by then.
        logger.info(line.strip())
    return lines


def select_targets(
    rows: list[AppRow], wanted: set[str], *, include_auth: bool
) -> tuple[list[AppRow], list[AppRow]]:
    """``(targets, skipped_for_auth)`` from the catalog.

    ``account_required`` apps are skipped by default and included only under
    ``--include-auth`` (AGENTS §3): a session that spends its budget stuck
    against a login wall collects nothing but the login wall.
    """
    targets = [
        row for row in rows if row.is_collectable and (not wanted or row.package_id in wanted)
    ]
    if include_auth:
        return targets, []
    skipped = [row for row in targets if row.needs_auth]
    return [row for row in targets if not row.needs_auth], skipped


def _declared_activities(adb: AdbClient, package: str) -> tuple[list[str], bool, dict[str, str]]:
    """``(activities, fixed_denominator, aliases)`` for coverage.

    ``catalog/activities.json`` (androguard over the APK manifest) is preferred
    and gives a FIXED denominator. It covers 41 of the 48 collection targets;
    the rest fall back to `dumpsys`, which lists only components carrying an
    intent filter and so is structurally incomplete — an activity launched
    internally never appears. A fixed denominator built from that scores a
    perfectly healthy run at 0%, so the fallback is paired with a dynamic total.
    """
    from monkey_collector.catalog_activities import ActivityCatalog

    catalog = ActivityCatalog.instance()
    declared = catalog.get_declared(package)
    if declared:
        return declared, True, catalog.get_aliases(package) or {}
    return adb.get_declared_activities(package), False, {}


def cmd_run(args: argparse.Namespace) -> int:
    """Host-pull collection loop over one or more catalog apps.

    Owns the sweep's log sink and nothing else: one collection produces one
    directory, log included, and the sink is released afterwards so a second
    sweep in the same process does not write both roots at once.
    """
    from monkey_collector.paths import run_log

    log_path = run_log(args.root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sink = logger.add(log_path, level="INFO")
    try:
        return _run_sweep(args)
    finally:
        logger.remove(sink)


def _run_sweep(args: argparse.Namespace) -> int:
    """The sweep itself: select targets, then drive each one to its budget."""
    from dataclasses import replace

    from monkey_collector.adb import AdbClient
    from monkey_collector.aig import AIG
    from monkey_collector.catalog import load_catalog
    from monkey_collector.config import parse_duration
    from monkey_collector.domain.activity_coverage import ActivityCoverageTracker
    from monkey_collector.domain.cost_tracker import CostTracker
    from monkey_collector.explore import Explorer
    from monkey_collector.llm.client import create_llm_client
    from monkey_collector.loop import CollectionLoop
    from monkey_collector.pagematch import MergePolicy, PageRegistry
    from monkey_collector.semantic import SemanticLabeler
    from monkey_collector.session import Session
    from monkey_collector.text_input import create_text_generator

    config = args.run_config
    collection = config.collection
    matching = config.page_matching

    rows = load_catalog()
    targets, skipped = select_targets(rows, set(args.apps) - {"all"}, include_auth=args.include_auth)
    if skipped:
        print(
            f"skipping {len(skipped)} account_required app(s); "
            f"pass --include-auth to include them"
        )
    if not targets:
        print("no collectable apps matched — nothing to do")
        return 0

    budget_mode = args.budget_mode or collection.budget_mode
    duration = (
        float(parse_duration(args.max_duration))
        if args.max_duration
        else float(collection.max_duration_sec)
    )
    max_steps = args.max_steps if args.max_steps else collection.max_steps
    # The inactive budget must not also stop the run: `time` means time.
    if budget_mode == "time":
        max_steps = 0
    else:
        duration = float("inf")

    seed = collection.seed if args.seed is None else args.seed
    # --input-mode overrides llm.input_mode for this invocation, and it is the
    # RESOLVED config that both the client and the generator see, so the flag
    # cannot be parsed and then quietly ignored.
    llm_config = replace(config.llm, input_mode=args.input_mode or config.llm.input_mode)

    adb = AdbClient(serial=args.serial or config.device.serial)
    print(f"device {adb.serial}: {len(targets)} app(s), budget {budget_mode}, seed {seed}")
    if args.prepare_device:
        prepare_device(adb)
    else:
        print("  device: preparation skipped (--no-prepare-device)")

    # Every app the catalog collects, not just this invocation's targets: a
    # screen of app B is not app A's data whether or not B is being collected
    # today, and `--apps A` must behave the same as a full sweep.
    catalog_packages = {row.package_id for row in rows if row.is_collectable}

    # Nothing on the device may be carrying state into this sweep. Each session
    # already starts its own app cold, but that says nothing about the OTHER 47:
    # a task left open by a previous run is what an app hands off to, and it is
    # what the launcher intent resumes. Measured on the sibling collector before
    # this existed: 31 open tasks after a few attempts, and apps reached each
    # other through them.
    stopped = 0
    for package in sorted(catalog_packages):
        try:
            adb.force_stop(package)
            stopped += 1
        except Exception as error:  # noqa: BLE001 - one refusal must not stop the sweep
            print(f"  could not stop {package} ({error})")
    print(f"stopped {stopped}/{len(catalog_packages)} catalog app(s) before starting")

    failures = 0
    for position, row in enumerate(targets, start=1):
        session = Session(
            row.package_id,
            raw_root(args.root),
            runtime_root(args.root),
            episode=row.package_id,
        )
        if session.is_complete and not args.force:
            print(f"[{position}/{len(targets)}] {row.package_id}: already complete, skipping")
            continue

        # open() creates runtime/apps/{package}/, which the two CSV trackers
        # below write into, so it has to come first.
        session.open(resume=not args.force)
        # BOTH trackers take the same branch. `initialize` opens its CSV with
        # "w": using it on a resumed session would truncate the file, and
        # activity_coverage.csv is a TIME SERIES whose whole value is being one
        # -- losing the earlier half leaves a plausible-looking curve that
        # starts from zero, with nothing in the data to say why.
        resumed = bool(session.observation_count) and not args.force
        declared, fixed, aliases = _declared_activities(adb, row.package_id)
        cost_tracker = CostTracker()
        coverage = ActivityCoverageTracker()
        if resumed:
            cost_tracker.resume(str(session.runtime))
            coverage.resume(
                str(session.runtime),
                declared,
                package=row.package_id,
                allow_dynamic_total=not fixed,
                aliases=aliases,
            )
        else:
            cost_tracker.initialize(str(session.runtime))
            coverage.initialize(
                str(session.runtime),
                declared,
                package=row.package_id,
                allow_dynamic_total=not fixed,
                aliases=aliases,
            )

        client = None
        if config.llm.semantic_labeling or llm_config.input_mode == "api":
            client = create_llm_client(cost_tracker, config=llm_config)
        labeler = SemanticLabeler(
            client=client,
            app_name=row.app_name,
            enabled=config.llm.semantic_labeling,
            min_elements_for_grouping=config.exploration.min_elements_for_grouping,
        )
        # A fresh graph per run, deliberately — see loop.py's module docstring
        # on why a resumed session must not load one.
        graph = AIG(package=row.package_id, semantic_labeling=labeler.active)
        if (session.root / "graph.json").exists():
            logger.warning(
                "{}: replacing an existing graph.json — page ids are minted per "
                "run, so a resumed session's graph starts over",
                row.package_id,
            )

        loop = CollectionLoop(
            adb,
            session,
            registry=PageRegistry(
                policy=MergePolicy(matching.merge_policy),
                max_diff_elements=matching.max_diff_elements,
                same_activity_only=matching.same_activity_only,
            ),
            graph=graph,
            explorer=Explorer(
                graph, row.package_id, config=config.exploration, seed=seed
            ),
            labeler=labeler,
            coverage=coverage,
            text_generator=create_text_generator(
                llm_config, llm_client=client, seed=seed
            ),
            llm_client=client,
            cost_tracker=cost_tracker,
            max_duration_sec=duration,
            max_steps=max_steps,
            action_delay_ms=collection.action_delay_ms,
            launch_settle_sec=collection.launch_settle_sec,
            sibling_packages=catalog_packages,
            seed=seed,
            stabilize={
                "max_wait_sec": collection.stabilize_max_wait_sec,
                "poll_ms": collection.stabilize_poll_ms,
                "pixel_threshold": collection.stabilize_pixel_threshold,
                "luma_delta": collection.stabilize_luma_delta,
                "low_res_width": collection.stabilize_low_res_width,
            },
        )
        print(f"[{position}/{len(targets)}] {row.app_name} ({row.package_id})")
        try:
            stats = loop.run()
        except KeyboardInterrupt:
            # Metadata is written by run()'s own exit path only on a clean end,
            # so record the interruption here — otherwise the session looks
            # untouched and a resume would restart its numbering.
            session.write_metadata(completed=False, extra={"stop_reason": "interrupted"})
            print("interrupted — session left resumable")
            raise
        except Exception as error:  # noqa: BLE001 - one bad app must not end the sweep
            failures += 1
            session.write_metadata(completed=False, extra={"stop_reason": f"error: {error}"})
            print(f"    FAILED: {error}")
            continue

        print(
            f"    {stats.triples} triples / {stats.observations} observations / "
            f"{stats.pages} pages / {stats.edges} edges / "
            f"{coverage.get_visited_count()}/{len(coverage.total_activities)} activities / "
            f"{stats.llm_calls} llm calls (${stats.cost_usd:.4f}) — {stats.stop_reason}"
        )
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# reset — one root, one removal
# ---------------------------------------------------------------------------


def cmd_reset(args: argparse.Namespace) -> int:
    """Delete a collection root's artifacts so the next run starts clean.

    Exists because "throw the pilot away before the real run" must be one
    reliable action. A leftover ``raw/`` beside a fresh export is
    indistinguishable from a consistent one, and a resumed session would
    continue the OLD observation numbering — older triples would then reference
    newer screens with nothing in the data to flag it.

    ``run.log`` is deliberately NOT deleted: the data can be re-collected, the
    record of what went wrong while collecting it cannot.
    """
    root = collection_root(args.root)
    if not root.exists():
        print(f"{root} does not exist — nothing to reset")
        return 0

    scopes: list[tuple[str, list[Path]]] = []
    if args.raw or args.all:
        scopes.append(("raw", [raw_root(args.root)]))
    if args.runtime or args.all:
        scopes.append(("runtime", [runtime_root(args.root)]))
    if args.export or args.all:
        exports = sorted(export_root(args.root).glob("stage1_*.jsonl"))
        exports += [p for p in (export_root(args.root) / "images",) if p.exists()]
        exports += [p for p in (export_root(args.root) / "export_meta.json",) if p.exists()]
        scopes.append(("export", exports))
    if not scopes:
        print("pick a scope: --raw / --runtime / --export / --all")
        return 2

    targets = [(name, p) for name, paths in scopes for p in paths if p.exists()]
    if not targets:
        print(f"{root}: nothing to delete in the selected scope(s)")
        return 0

    for name, path in targets:
        size = (
            sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            if path.is_dir()
            else path.stat().st_size
        )
        print(f"  {name:<8} {path}  ({size / 1e6:.1f} MB)")
    if args.dry_run:
        print("dry-run: nothing deleted")
        return 0

    for _, path in targets:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    print(f"deleted {len(targets)} path(s) under {root}")
    return 0


# ---------------------------------------------------------------------------
# review — human filtering of the collected corpus
# ---------------------------------------------------------------------------


def cmd_review(args: argparse.Namespace) -> int:
    """Serve the review UI over ``{root}/raw``, writing verdicts to ``{root}/review``.

    Read-only against the corpus by construction (see
    :mod:`monkey_collector.review`), so it is safe to open while a sweep is
    still collecting the apps further down the list. Touches no device.
    """
    from monkey_collector.review.server import serve
    from monkey_collector.review.store import sanitize_reviewer

    config = args.run_config
    return serve(
        raw_root(args.root),
        review_root(args.root),
        host=args.host,
        port=args.port,
        device_size=(config.device.width, config.device.height),
        reviewer=sanitize_reviewer(args.reviewer or os.environ.get("USER") or "anon"),
        open_browser=not args.no_browser,
    )


# ---------------------------------------------------------------------------
# export — collected triples -> EXP08 Stage-1 jsonl
# ---------------------------------------------------------------------------


def cmd_export(args: argparse.Namespace) -> int:
    """Convert collected triples into the Stage-1 (NEXT_STATE_PREDICTION) jsonl.

    Reads ``{root}/raw`` and writes the three jsonl splits, ``images/`` and
    ``export_meta.json`` at the root itself, so one collection stays one
    directory. Touches no device: the corpus on disk is the only input.
    """
    from monkey_collector.export import SPLIT_ID, SPLIT_OOD, SPLIT_TRAIN, Exporter

    config = args.run_config
    exporter = Exporter(
        raw_root(args.root),
        runtime_root(args.root),
        export_root(args.root),
        target_size=config.export.target_size,
        # Only a session that recorded no `wm size` falls back to this; the
        # frame each app is actually written in comes from its own metadata.
        device_size=(config.device.width, config.device.height),
        ood_apps=args.ood_apps if args.ood_apps is not None else config.export.ood_apps,
        id_ratio=args.id_ratio if args.id_ratio is not None else config.export.id_ratio,
        seed=args.seed if args.seed is not None else config.collection.seed,
        keep_unchanged=args.keep_unchanged,
        review_dir=review_root(args.root),
        use_review=not args.ignore_review,
        strict_review=args.strict_review,
    )
    stats = exporter.run()
    if stats.review_stale and args.strict_review:
        # Checked first: under --strict-review the exporter refuses before
        # writing, so "nothing was written" here is the REFUSAL, not an empty
        # corpus, and must not be reported as one.
        print(
            f"monkey-collect: {stats.review_stale} verdict(s) no longer match their "
            "screens; nothing was exported. Re-review those apps, or drop "
            "--strict-review to export without them.",
            file=sys.stderr,
        )
        return 2
    if not stats.total_written:
        print("nothing exported — no collected session produced a usable triple")
        return 1

    print(f"{stats.apps} app(s), {stats.triples_seen} triples seen")
    for split in (SPLIT_TRAIN, SPLIT_ID, SPLIT_OOD):
        print(f"  stage1_{split}.jsonl : {stats.written.get(split, 0)}")
    print(
        f"  dropped: {stats.dropped_unchanged} unchanged, "
        f"{stats.dropped_unparsable} unparsable, "
        f"{stats.dropped_missing_files} missing files, "
        f"{stats.dropped_foreign} foreign, "
        f"{stats.dropped_duplicate_step} duplicate step, "
        f"{stats.dropped_unknown_action} unknown action, "
        f"{stats.dropped_excluded} excluded by review"
    )
    if stats.review:
        print(
            f"  review: {stats.review.get('verdicts', 0)} verdict(s), "
            f"{stats.reviewed_kept} kept, {stats.dropped_excluded} excluded"
        )
    elif not args.ignore_review:
        print("  review: no verdicts yet — run `monkey-collect review` to filter by hand")
    if stats.review_stale:
        # Loud, and never silently applied: these verdicts were made about
        # screens that are no longer at those steps.
        print(
            f"  WARNING: {stats.review_stale} verdict(s) no longer match their screens "
            "and were NOT applied — the corpus was re-collected under an old review/. "
            "Re-review those apps.",
            file=sys.stderr,
        )
    # An app filtered to almost nothing is otherwise only visible as a smaller
    # grand total — and if it is the OOD holdout, that number IS the eval set.
    thin = sorted(
        (count, package)
        for package, count in stats.written_by_app.items()
        if count < THIN_APP_RECORDS
    )
    for count, package in thin:
        print(
            f"  WARNING: {package} contributed only {count} record(s)"
            f"{' — it is held out for OOD' if package in stats.ood_apps else ''}",
            file=sys.stderr,
        )
    # The contract's headline number (0 of 20,000 in the canonical corpus): if
    # this is not 0, the rescale or a recorded device size is wrong.
    print(f"  action coordinates outside their frame: {stats.coords_out_of_frame}")
    if stats.ood_apps:
        print(f"  held out for OOD: {', '.join(stats.ood_apps)}")
    print(f"  -> {export_root(args.root)}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monkey-collect",
        description=(
            "Monkey-Collector — host-pull Android GUI data collector. "
            "`catalog`, `sync-installed`, `provision`, `reset`, `run`, `review` "
            "and `export` are implemented against the finished host-pull foundation "
            "(adb.py, paths.py, xml/, pagematch.py, stabilize.py, session.py, "
            "catalog.py, provision.py), the LLM-Explorer collection loop "
            "(loop.py, explore.py, aig.py, semantic.py), the human filtering UI "
            "(review/) and the EXP08 Stage-1 export (export.py)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Implemented: catalog, sync-installed, provision, reset, run, review, export.\n"
            "WARNING: `run` drives a REAL, logged-in device and has no action "
            "guard by design (AGENTS \u00a70.5) — exploration can send messages or "
            "post content."
        ),
    )
    from monkey_collector import __version__
    from monkey_collector.catalog import VALID_STATUSES

    parser.add_argument("--version", action="version", version=f"monkey-collector {__version__}")
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to run.yaml. Resolved before any subcommand runs; a missing or "
            "unparseable file named here is an error (exit 2). Omitted: config/run.yaml "
            "is used when present, builtin defaults when it is absent."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # -- catalog ---------------------------------------------------------------
    p_catalog = sub.add_parser(
        "catalog",
        help="List or summarise the app catalog.",
        description="Read catalog/apps.csv and list or summarise its rows.",
    )
    p_catalog.add_argument("--catalog", default=None, help="Catalog CSV (default: catalog/apps.csv).")
    p_catalog.add_argument(
        "--tier",
        choices=["androidworld_fdroid", "fdroid", "playstore"],
        help="Only rows in this tier.",
    )
    p_catalog.add_argument(
        "--installed",
        choices=["true", "false"],
        help="Only rows whose installed column has this value.",
    )
    p_catalog.add_argument(
        "--auth",
        choices=["none", "account_optional", "account_required"],
        help="Only rows with this auth_required value.",
    )
    p_catalog.add_argument(
        "--status",
        choices=sorted(VALID_STATUSES),
        help=(
            "Only rows with this catalog-curation status. Independent of --installed, "
            "which is the device-measured fact — see catalog.py for why they differ."
        ),
    )
    p_catalog.add_argument(
        "--exclude-auth",
        action="store_true",
        help="Drop account_required rows (the runner's default posture).",
    )
    p_catalog.add_argument("--stats", action="store_true", help="Print a summary instead of rows.")
    p_catalog.set_defaults(func=cmd_catalog)

    # -- sync-installed ----------------------------------------------------------
    p_sync = sub.add_parser(
        "sync-installed",
        help="Refresh the catalog's installed column from the device.",
        description=(
            "Read `adb pm list packages` and rewrite catalog/apps.csv's `installed` "
            "column to match. ONLY that column: `status` is the human curation verdict "
            "and is never derived from the device."
        ),
    )
    p_sync.add_argument("--catalog", default=None, help="Catalog CSV (default: catalog/apps.csv).")
    p_sync.add_argument("--serial", default=None, help="Device serial (default: config device.serial).")
    p_sync.add_argument("--dry-run", action="store_true", help="Report the diff, write nothing.")
    p_sync.set_defaults(func=cmd_sync_installed)

    # -- provision ----------------------------------------------------------
    p_prov = sub.add_parser(
        "provision",
        help="Install the APKs a collection run needs.",
        description=(
            "Resolve each target APK through Monkey-Collector's own cache, then "
            "AndroidWorld's version-pinned APKs, then f-droid.org, install it, and "
            "VERIFY by re-querying the device. Failures accumulate in "
            "catalog/PROVISION_MISSING.json."
        ),
    )
    p_prov.add_argument("--catalog", default=None, help="Catalog CSV (default: catalog/apps.csv).")
    p_prov.add_argument("--serial", default=None, help="Device serial (default: config device.serial).")
    p_prov.add_argument(
        "--only",
        nargs="+",
        default=None,
        metavar="PKG",
        help="Limit the run to these package ids. Out-of-scope ledger records are kept.",
    )
    p_prov.add_argument(
        "--force",
        action="store_true",
        help="Reinstall rows already present on the device. Never overrides status=excluded.",
    )
    p_prov.add_argument(
        "--dry-run", action="store_true", help="Resolve and report; install nothing."
    )
    p_prov.add_argument(
        "--no-download",
        action="store_true",
        help="Local sources only — do not reach out to f-droid.org.",
    )
    p_prov.add_argument(
        "--apk-cache", default=None, help="Override the local APK cache directory."
    )
    p_prov.add_argument(
        "--android-world", default=None, help="Override AndroidWorld's pinned-APK directory."
    )
    p_prov.add_argument(
        "--ledger",
        default=None,
        help="Failure ledger path (default: catalog/PROVISION_MISSING.json).",
    )
    p_prov.set_defaults(func=cmd_provision)

    # -- run ------------------------------------------------------------------
    p_run = sub.add_parser(
        "run",
        help="Run the host-pull collection loop over the catalog.",
        description=(
            "Drive each collectable app with the LLM-Explorer policy, writing "
            "observations, triples.jsonl and graph.json under one collection root. "
            "account_required apps are skipped unless --include-auth."
        ),
    )
    p_run.add_argument("--apps", nargs="+", default=["all"], help="Package ids, or 'all'.")
    p_run.add_argument(
        "--serial", default=None, help="Device serial (default: config device.serial)."
    )
    p_run.add_argument(
        "--budget-mode",
        choices=["steps", "time"],
        default=None,
        help="Override collection.budget_mode (default: time).",
    )
    p_run.add_argument(
        "--max-duration",
        default=None,
        metavar="DURATION",
        help='Override collection.max_duration, e.g. "2h" / "120m" / "7200s". '
        "Used when budget_mode is time.",
    )
    p_run.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Override collection.max_steps. Used when budget_mode is steps.",
    )
    p_run.add_argument(
        "--seed", type=int, default=None, help="Override collection.seed (explorer RNG)."
    )
    p_run.add_argument(
        "--input-mode",
        choices=["api", "random"],
        default=None,
        help=(
            "Override llm.input_mode for input-text generation. `random` uses canned "
            "samples and issues no API call for text; semantic labelling is separate "
            "(llm.semantic_labeling)."
        ),
    )
    p_run.add_argument(
        "--root",
        default=DEFAULT_ROOT,
        help=(
            f"The single collection root (default: {DEFAULT_ROOT}). Holds raw/, "
            "runtime/, run.log and the export side by side, so one run is one directory."
        ),
    )
    p_run.add_argument(
        "--force",
        action="store_true",
        help="Re-collect apps already marked complete, restarting their numbering.",
    )
    p_run.add_argument(
        "--include-auth",
        action="store_true",
        help="Also collect account_required apps (skipped by default).",
    )
    p_run.add_argument(
        "--no-prepare-device",
        dest="prepare_device",
        action="store_false",
        help=(
            "Skip the AGENTS §1 device preconditions (screen stay-on, OTA prompt "
            "suppression). They CHANGE system settings on the target device; the "
            "default is to apply them because a locked screen or a GMS update modal "
            "makes every dump belong to another package."
        ),
    )
    p_run.set_defaults(func=cmd_run, prepare_device=True)

    # -- reset ----------------------------------------------------------------
    p_reset = sub.add_parser(
        "reset",
        help="Delete a collection root's artifacts so the next run starts clean.",
    )
    p_reset.add_argument("--root", default=DEFAULT_ROOT, help=f"Collection root (default: {DEFAULT_ROOT}).")
    p_reset.add_argument("--raw", action="store_true", help="Delete raw/ (the collected corpus).")
    p_reset.add_argument("--runtime", action="store_true", help="Delete runtime/ (session state).")
    p_reset.add_argument("--export", action="store_true", help="Delete the Stage-1 jsonl + images.")
    p_reset.add_argument("--all", action="store_true", help="Delete every artifact under the root.")
    p_reset.add_argument("--dry-run", action="store_true", help="List what would go, delete nothing.")
    p_reset.set_defaults(func=cmd_reset)

    # -- export ---------------------------------------------------------------
    # -- review ----------------------------------------------------------------
    p_review = sub.add_parser(
        "review",
        help="Open the human-filtering UI over a collected corpus.",
        description=(
            "Serve a local web UI over {root}/raw so a person can look at every "
            "before/action/after triple, per app, and exclude the ones that must "
            "not reach the export. READ-ONLY against raw/ and runtime/: safe to "
            "open while a sweep is still collecting. Verdicts are appended to "
            "{root}/review/by-<reviewer>.jsonl and `export` applies them by "
            "default. Excluding never deletes collected bytes."
        ),
    )
    p_review.add_argument(
        "--root",
        default=DEFAULT_ROOT,
        help=f"The collection root to review (default: {DEFAULT_ROOT}).",
    )
    p_review.add_argument("--port", type=int, default=8700, help="Port to serve on (default: 8700).")
    p_review.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Interface to bind (default: 127.0.0.1). The corpus is screenshots of "
            "a REAL, logged-in device — binding anything else publishes it."
        ),
    )
    p_review.add_argument(
        "--reviewer",
        default=None,
        help=(
            "Name recorded on this session's verdicts, and the file they go to "
            "(by-<name>.jsonl). Defaults to $USER. Reviewers splitting the apps "
            "between them get one file each, so nothing merges by hand."
        ),
    )
    p_review.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser window."
    )
    p_review.set_defaults(func=cmd_review)

    p_export = sub.add_parser(
        "export",
        help="Convert collected triples into the EXP08 Stage-1 jsonl.",
        description=(
            "Read {root}/raw and write stage1_train.jsonl, stage1_test_id.jsonl, "
            "stage1_test_ood.jsonl, images/ and export_meta.json at the root. "
            "Action coordinates and data-bbox are both rescaled into the frame "
            "derived from the device size each session recorded (ARCHITECTURE §8.2) "
            "— not into export.target_size, which is checked against it. "
            "Reads the corpus only; no device is touched."
        ),
    )
    p_export.add_argument(
        "--root",
        default=DEFAULT_ROOT,
        help=f"The collection root to export (default: {DEFAULT_ROOT}).",
    )
    p_export.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override collection.seed for the app split and the ID sample.",
    )
    p_export.add_argument(
        "--keep-unchanged",
        action="store_true",
        help=(
            "Also export triples whose screen did not change. Off by default: "
            "they are real observations, but a corpus dominated by 'nothing "
            "happened' teaches the model to predict its own input."
        ),
    )
    p_export.add_argument(
        "--ood-apps",
        type=float,
        default=None,
        metavar="FRACTION",
        help=(
            "Fraction of APPS held out entirely for OOD eval "
            "(default: export.ood_apps). Independent of --id-ratio."
        ),
    )
    p_export.add_argument(
        "--id-ratio",
        type=float,
        default=None,
        metavar="FRACTION",
        help=(
            "Fraction of each SEEN app's triples reserved for ID eval, sampled per "
            "app (default: export.id_ratio). Independent of --ood-apps."
        ),
    )
    p_export.add_argument(
        "--ignore-review",
        action="store_true",
        help=(
            "Export every triple, ignoring {root}/review. Verdicts are applied by "
            "default so a filter cannot be forgotten; this is the explicit way to "
            "produce the unfiltered corpus for comparison."
        ),
    )
    p_export.add_argument(
        "--strict-review",
        action="store_true",
        help=(
            "Exit 2 when a verdict no longer matches the screens at its step "
            "(the corpus was re-collected under an old review/). Without it such "
            "verdicts are reported and skipped, never applied."
        ),
    )
    p_export.set_defaults(func=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse argv, resolve the run config, dispatch. Returns the process exit code.

    Exit codes: 0 success, 1 no subcommand / empty result set, 2 bad config.
    """
    from monkey_collector.config import ConfigError, load_run_config

    parser = build_parser()
    args = parser.parse_args(argv)

    # Resolve config BEFORE dispatch, for every subcommand. `--config` pointing at a
    # missing or unparseable file must fail here and now — see the module docstring.
    try:
        args.run_config = load_run_config(args.config)
    except ConfigError as exc:
        print(f"monkey-collect: {exc}", file=sys.stderr)
        return 2

    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
