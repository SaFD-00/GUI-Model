"""Read/write ``catalog/apps.csv`` — the target-app catalog.

Stdlib only (``csv``, ``dataclasses``, ``pathlib``). The ``catalog`` subcommand
must keep working even when the optional runtime deps (openai / pillow) are
broken or absent, and the test-suite import surface stays third-party-free.

The CSV was resolved against the live Pixel 6 and is **committed data**, not a
generated artifact. Columns::

    tier,category,sub_category,app_name,package_id,source,priority,
    auth_required,installed,device_verified,status,notes

Semantics that other modules depend on:

- ``tier``           androidworld_fdroid | fdroid | playstore
- ``auth_required``  none | account_optional | account_required.
                     The runner SKIPS ``account_required`` apps by default and
                     includes them only under ``--include-auth``.
- ``installed``      literal ``"true"`` / ``"false"`` — parsed by explicit string
                     comparison, never ``bool(field)`` (``bool("false")`` is True).
- ``status``         ok | clone_accepted | not_installed | excluded.

``installed`` AND ``status`` ARE INDEPENDENT COLUMNS — do not conflate them
=========================================================================

They answer two different questions and are maintained by two different owners:

===============  =========================  ===================================
column           question                   owner
===============  =========================  ===================================
``installed``    is the APK on the device    the DEVICE. Measured against the
                 right now?                  live Pixel 6; milestone 3's
                                             ``sync-installed`` refreshes it
                                             from ``adb pm list packages``.
``status``       what did CURATION decide    a HUMAN. Recorded once, by hand,
                 about this row?             when the catalog was adjudicated.
===============  =========================  ===================================

So the two columns disagreeing is the NORMAL state, not a bug to reconcile:
most rows are ``status=ok`` (the row is a legitimate, correctly-identified
target) while ``installed=false`` (it simply is not on this particular handset
today). ``status=not_installed`` means something stronger and rarer — curation
concluded the app could not be installed at all.

``clone_accepted`` marks a row where the package on the device is a re-skinned
clone rather than the canonical publisher's build, and the user chose to collect
it anyway, because the device's actual contents are what we can actually drive.

``excluded`` marks a row the user decided NOT to collect. It is the one status
that changes what the runner does: :attr:`AppRow.is_collectable` is False for an
excluded row regardless of ``installed``, so milestone 4 never drives it. The
row is kept rather than deleted so the reason survives in ``notes`` — otherwise
a later catalog pass would "helpfully" re-add the app and rediscover the same
dead end. The four excluded rows (2026-08-28) could not be provisioned:
Simple Contacts Pro and GnuCash are gone from the F-Droid index, Metro is on
F-Droid but its ``/repo`` binary path fails TLS verification here, and TickTick
is a split APK whose base alone fails with ``INSTALL_FAILED_MISSING_SPLIT``.

**Milestone 3's ``sync-installed`` must write the ``installed`` column ONLY.**
Deriving ``status`` from ``installed`` (or vice versa) would destroy the
curation record on the first sync, and it would do so silently — every row would
still look plausible. ``tests/test_catalog.py`` pins the independence for this
reason.

The ``PENDING`` package-id sentinel (:data:`PENDING_PACKAGE`) is retained in
code but NO row carries it any more: the catalog is fully resolved, and the
tests assert it stays that way. It survives as a guard, not as live data.

.. note::
   :func:`write_catalog` DOES reproduce the committed ``catalog/apps.csv``
   byte-for-byte — measured 2026-08-28, ``read_bytes()`` equal on both sides.
   An earlier version of this docstring warned it did not; the fear was that
   ``csv.writer``'s QUOTE_MINIMAL would drop the quoting on the four
   gratuitously-quoted rows, but each of them carries an embedded ``"`` or a
   comma, so QUOTE_MINIMAL quotes them anyway. The file is CRLF-terminated,
   which is also what the excel dialect emits. ``sync-installed`` is the
   sanctioned writer of this file and rewrites the ``installed`` column only;
   ``tests/test_provision.py`` pins the byte-identity so a reformat cannot ride
   in on a sync. Compare with ``read_bytes``, never ``read_text`` — the latter
   normalises newlines and would pass through a CRLF-to-LF rewrite.
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from monkey_collector.paths import catalog_path

FIELDNAMES: tuple[str, ...] = (
    "tier",
    "category",
    "sub_category",
    "app_name",
    "package_id",
    "source",
    "priority",
    "auth_required",
    "installed",
    "device_verified",
    "status",
    "notes",
)

#: ``package_id`` sentinel for rows whose real package could not be resolved.
PENDING_PACKAGE = "PENDING"

#: ``auth_required`` value that the runner skips unless ``--include-auth``.
AUTH_REQUIRED = "account_required"

VALID_TIERS: frozenset[str] = frozenset({"androidworld_fdroid", "fdroid", "playstore"})
VALID_AUTH: frozenset[str] = frozenset({"none", "account_optional", "account_required"})
#: Catalog-CURATION states. Not a mirror of the ``installed`` column — see the
#: module docstring on why the two disagree by design.
VALID_STATUSES: frozenset[str] = frozenset(
    {"ok", "clone_accepted", "not_installed", "excluded"}
)

#: The one ``status`` that suppresses collection — see the module docstring.
EXCLUDED_STATUS = "excluded"


@dataclass(frozen=True)
class AppRow:
    """One row of ``catalog/apps.csv``. All fields are kept as raw strings.

    The CSV is the single source of truth and its text is preserved verbatim;
    typed views are exposed as properties so a round-trip never reformats a value.
    """

    tier: str
    category: str
    sub_category: str
    app_name: str
    package_id: str
    source: str
    priority: str
    auth_required: str
    installed: str
    device_verified: str
    status: str
    notes: str

    @property
    def is_installed(self) -> bool:
        """Device fact: the APK was present at the last sync.

        True only for the literal string ``"true"``. Never ``bool(self.installed)``
        — ``bool("false")`` is ``True``. Independent of :attr:`status`.
        """
        return self.installed == "true"

    @property
    def is_device_verified(self) -> bool:
        return self.device_verified == "true"

    @property
    def needs_auth(self) -> bool:
        """True when the app gates behind an account (runner skips by default)."""
        return self.auth_required == AUTH_REQUIRED

    @property
    def is_pending(self) -> bool:
        """True for rows awaiting package-id adjudication.

        No committed row is pending any more (the catalog is fully resolved);
        this stays as a guard against an unresolved row being reintroduced.
        """
        return self.package_id == PENDING_PACKAGE

    @property
    def is_excluded(self) -> bool:
        """True for a row the user decided not to collect."""
        return self.status == EXCLUDED_STATUS

    @property
    def is_collectable(self) -> bool:
        """Installed, package resolved, not excluded — the bar for M4's runner.

        ``is_excluded`` is checked here rather than at the call site so a row
        cannot be collected by a code path that forgot to filter it.
        """
        return self.is_installed and not self.is_pending and not self.is_excluded

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def load_catalog(path: str | Path | None = None) -> list[AppRow]:
    """Read the catalog CSV into :class:`AppRow` objects, preserving file order.

    Args:
        path: CSV to read. Defaults to ``catalog/apps.csv`` under the project root.

    Raises:
        FileNotFoundError: if the CSV is missing.
        ValueError: if the header does not match :data:`FIELDNAMES` exactly —
            a silently-renamed column would corrupt every downstream filter.
    """
    csv_path = Path(path) if path is not None else catalog_path()
    if not csv_path.exists():
        raise FileNotFoundError(f"App catalog not found: {csv_path}")

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = tuple(reader.fieldnames or ())
        if header != FIELDNAMES:
            raise ValueError(
                f"Unexpected catalog header in {csv_path}.\n"
                f"  expected: {FIELDNAMES}\n"
                f"  actual:   {header}"
            )
        known = {f.name for f in fields(AppRow)}
        return [
            AppRow(**{k: (v if v is not None else "") for k, v in row.items() if k in known})
            for row in reader
        ]


def write_catalog(rows: Iterable[AppRow], path: str | Path | None = None) -> Path:
    """Write *rows* back out as CSV. See the module-level warning before using.

    Returns the path written.
    """
    csv_path = Path(path) if path is not None else catalog_path()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FIELDNAMES))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())
    return csv_path


def filter_rows(
    rows: Sequence[AppRow],
    *,
    tier: str | None = None,
    installed: bool | None = None,
    auth: str | None = None,
    status: str | None = None,
    include_auth: bool = True,
) -> list[AppRow]:
    """Filter catalog rows. ``None`` means "do not filter on this axis".

    Args:
        tier: exact ``tier`` match.
        installed: match on the parsed boolean, not the raw string.
        auth: exact ``auth_required`` match.
        status: exact ``status`` match. Curation state, NOT a proxy for
            ``installed`` — filtering ``status="not_installed"`` is not the same
            query as ``installed=False`` (see the module docstring).
        include_auth: when False, drop ``account_required`` rows — the runner's
            default posture. Independent of the ``auth`` filter above.
    """
    out = list(rows)
    if tier is not None:
        out = [r for r in out if r.tier == tier]
    if installed is not None:
        out = [r for r in out if r.is_installed is installed]
    if auth is not None:
        out = [r for r in out if r.auth_required == auth]
    if status is not None:
        out = [r for r in out if r.status == status]
    if not include_auth:
        out = [r for r in out if not r.needs_auth]
    return out


def catalog_stats(rows: Sequence[AppRow]) -> dict[str, object]:
    """Summary counts used by ``atlas-collect catalog --stats``."""
    return {
        "total": len(rows),
        "tier": dict(Counter(r.tier for r in rows)),
        "installed": sum(1 for r in rows if r.is_installed),
        "not_installed": sum(1 for r in rows if not r.is_installed),
        "device_verified": sum(1 for r in rows if r.is_device_verified),
        "auth_required": dict(Counter(r.auth_required for r in rows)),
        "status": dict(Counter(r.status for r in rows)),
        "category": dict(Counter(r.category for r in rows)),
        "pending": sum(1 for r in rows if r.is_pending),
        "excluded": sum(1 for r in rows if r.is_excluded),
        "collectable": sum(1 for r in rows if r.is_collectable),
    }


__all__ = [
    "AUTH_REQUIRED",
    "EXCLUDED_STATUS",
    "FIELDNAMES",
    "PENDING_PACKAGE",
    "VALID_AUTH",
    "VALID_STATUSES",
    "VALID_TIERS",
    "AppRow",
    "catalog_stats",
    "filter_rows",
    "load_catalog",
    "write_catalog",
]
