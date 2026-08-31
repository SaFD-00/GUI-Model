"""Human verdicts on collected triples: an append-only log, folded on read.

WHAT A VERDICT IS
=================

One line per decision, keyed by ``(package, step)``::

    {"package": "org.tasks", "step": 42, "before": 85, "after": 86,
     "verdict": "exclude", "reason": "no_effect", "note": "",
     "key": "9f1c...", "reviewer": "bsw", "at": "2026-08-30T19:02:11+09:00",
     "rule": "duplicate"}

Three states, not two: ``unreviewed`` (no line at all), ``keep`` and ``exclude``.
The third exists so progress has a denominator -- "87% reviewed, 12% excluded" is
a sentence only when "looked at it and it was fine" is recorded, not inferred
from silence.

APPEND-ONLY, ONE FILE PER REVIEWER
==================================

Verdicts are appended, never rewritten: a crash mid-session costs the last line
rather than the file, and the reviewer's own history stays readable. Folding
takes the LAST line for a ``(package, step)``, ordered by ``at`` and then by
filename, so a correction always wins over what it corrects.

One file per reviewer (``by-{name}.jsonl``) because apps are split between
people. Two reviewers appending to one file over a shared checkout would
interleave partial lines; two files merge with no coordination at all and carry
their own attribution for free.

IDENTITY IS ``(package, step)``; THE HASH IS A VALIDITY CHECK
=============================================================

The obvious alternative -- key each verdict by a hash of its content -- is
wrong here. Content hashes COLLIDE on genuinely duplicate triples, and this
corpus is full of them: one app repeated a single identical (before, action,
after) 369 times. A content key would make one verdict silently govern 369
steps, which is a different feature (see :mod:`monkey_collector.review.rules`,
where fanning a group's decision out to its members is explicit and recorded).

But the step number alone is not safe either. ``reset --raw`` followed by a
re-collection restarts observation numbering while ``review/`` survives, so
step 42 comes to mean a different screen. So every verdict also carries
:func:`identity_key`, a hash of the two dumps and the action. It is never used
to LOOK UP a verdict -- only to check, at export time, that the verdict is still
about the screens it was made about. A mismatch is STALE: not applied, counted,
and reported. Silence there is the failure mode this whole field exists to
prevent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

#: The three states. ``unreviewed`` is the absence of a verdict, never a stored
#: value, so it is not in this set -- storing it would make "cleared" and "never
#: looked at" the same thing to every consumer.
VERDICTS = ("keep", "exclude")

#: Fixed reason vocabulary. Free-text lives in ``note`` beside it: a taxonomy
#: cannot be backfilled from prose after the fact, and prose cannot be counted.
REASONS: dict[str, str] = {
    "wrong_app": "앱 밖 화면 (런처/시스템/형제 앱)",
    "no_effect": "액션이 아무 것도 하지 않음",
    "nondeterm": "액션과 무관한 변화 (광고·타이머·토스트)",
    "broken": "스샷/XML 깨짐, 로딩 스켈레톤",
    "dialog_noise": "권한창·OTA·시스템 다이얼로그",
    "pii": "개인정보 노출",
    "other": "기타 (메모 참고)",
}

_REVIEWER_RE = re.compile(r"[^A-Za-z0-9._-]+")
_FILE_RE = re.compile(r"^by-(?P<name>.+)\.jsonl$")


def sanitize_reviewer(name: str) -> str:
    """A reviewer name safe to put in a filename, never empty.

    The name reaches the filesystem as ``by-{name}.jsonl``, so it is stripped to
    a conservative alphabet rather than escaped: a reviewer called ``../../etc``
    must not be able to name a path, and there is no reason a human's handle
    needs a separator in it.
    """
    cleaned = _REVIEWER_RE.sub("-", (name or "").strip()).strip("-.")
    return cleaned[:40] or "anon"


def identity_key(before_xml: str, after_xml: str, action: dict[str, Any]) -> str:
    """Hash of the two RAW dumps and the action -- the verdict's validity check.

    Raw dumps rather than the encoded html-like form on purpose: the encoding is
    a moving target (the parser can be improved, the pixel budget can change),
    and a verdict must not go stale because the *renderer* changed. The bytes
    that came off the device are the thing the human actually judged.

    ``sort_keys`` so a re-serialized action with the same fields in another
    order hashes the same; a genuinely different action is a different key.
    """
    digest = hashlib.sha1()  # noqa: S324 - identity, not security
    digest.update(before_xml.encode("utf-8", "replace"))
    digest.update(b"\x00")
    digest.update(after_xml.encode("utf-8", "replace"))
    digest.update(b"\x00")
    digest.update(json.dumps(action, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class Verdict:
    """One folded decision about one step."""

    package: str
    step: int
    verdict: str
    reason: str = ""
    note: str = ""
    key: str = ""
    reviewer: str = ""
    at: str = ""
    rule: str = ""
    before: int = -1
    after: int = -1

    @property
    def is_exclude(self) -> bool:
        return self.verdict == "exclude"

    def as_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "step": self.step,
            "verdict": self.verdict,
            "reason": self.reason,
            "note": self.note,
            "key": self.key,
            "reviewer": self.reviewer,
            "at": self.at,
            "rule": self.rule,
            "before": self.before,
            "after": self.after,
        }


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class DecisionStore:
    """Every reviewer's log under one ``review/`` directory, folded per step.

    Construction reads; :meth:`record` appends. Nothing here ever touches
    ``raw/`` or ``runtime/`` -- a collection can be running while this is open,
    and the tool that owns this store must stay a reader of the corpus.
    """

    directory: Path
    #: ``(package, step) -> Verdict``, latest wins.
    verdicts: dict[tuple[str, int], Verdict] = field(default_factory=dict)
    #: Lines that could not be parsed, per file. A partially written last line
    #: is normal (another process may be appending); a count is enough.
    malformed: int = 0

    @classmethod
    def load(cls, directory: str | Path) -> DecisionStore:
        store = cls(Path(directory))
        store.reload()
        return store

    def reload(self) -> None:
        self.verdicts = {}
        self.malformed = 0
        if not self.directory.is_dir():
            return
        rows: list[tuple[str, str, dict[str, Any]]] = []
        for path in sorted(self.directory.glob("by-*.jsonl")):
            match = _FILE_RE.match(path.name)
            reviewer = match.group("name") if match else ""
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    self.malformed += 1
                    continue
                if not isinstance(raw, dict):
                    self.malformed += 1
                    continue
                raw.setdefault("reviewer", reviewer)
                rows.append((str(raw.get("at") or ""), path.name, raw))
        # Sorted by (at, filename) so a correction made later wins regardless of
        # which reviewer's file it landed in, and the order is total even when
        # two verdicts share a timestamp.
        rows.sort(key=lambda row: (row[0], row[1]))
        for _, _, raw in rows:
            package = str(raw.get("package") or "")
            try:
                step = int(raw["step"])
            except (KeyError, TypeError, ValueError):
                self.malformed += 1
                continue
            verdict = str(raw.get("verdict") or "")
            # `clear` is a REAL line, not a malformed one: it is how a withdrawn
            # decision stays in the append-only history. Skipping it here made
            # `undo` look like it worked and then resurrect the verdict on the
            # next load -- the fold and the in-memory state would disagree, and
            # only the fold reaches the export.
            if not package or verdict not in (*VERDICTS, "clear"):
                self.malformed += 1
                continue
            if verdict == "clear":
                self.verdicts.pop((package, step), None)
                continue
            self.verdicts[(package, step)] = Verdict(
                package=package,
                step=step,
                verdict=verdict,
                reason=str(raw.get("reason") or ""),
                note=str(raw.get("note") or ""),
                key=str(raw.get("key") or ""),
                reviewer=str(raw.get("reviewer") or ""),
                at=str(raw.get("at") or ""),
                rule=str(raw.get("rule") or ""),
                before=int(raw.get("before", -1) or -1),
                after=int(raw.get("after", -1) or -1),
            )

    # -- reading -------------------------------------------------------------

    def get(self, package: str, step: int) -> Verdict | None:
        return self.verdicts.get((package, step))

    def for_package(self, package: str) -> dict[int, Verdict]:
        return {
            step: verdict
            for (pkg, step), verdict in self.verdicts.items()
            if pkg == package
        }

    def counts(self, package: str) -> dict[str, int]:
        folded = self.for_package(package)
        return {
            "keep": sum(1 for v in folded.values() if v.verdict == "keep"),
            "exclude": sum(1 for v in folded.values() if v.verdict == "exclude"),
        }

    def generation(self) -> dict[str, Any]:
        """What produced the current fold: verdict count and per-file hashes.

        Written into ``export_meta.json``. Applying exclusions reshuffles the
        ID/train draw for the same seed, so two exports of "the same" root are
        not comparable without knowing which set of verdicts each one saw.
        """
        files: dict[str, str] = {}
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("by-*.jsonl")):
                digest = hashlib.sha1()  # noqa: S324 - provenance, not security
                digest.update(path.read_bytes())
                files[path.name] = digest.hexdigest()[:16]
        return {
            "verdicts": len(self.verdicts),
            "excluded": sum(1 for v in self.verdicts.values() if v.is_exclude),
            "files": files,
        }

    # -- writing (the ONLY thing in this package that writes) ----------------

    def record(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        reviewer: str,
    ) -> int:
        """Append verdicts for one reviewer and fold them in. Returns the count.

        A row with ``verdict: "clear"`` removes the decision instead of storing
        one -- the log keeps the clearing line (so the history stays honest)
        while the fold drops back to ``unreviewed``.
        """
        name = sanitize_reviewer(reviewer)
        path = self.directory / f"by-{name}.jsonl"
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = _now()
        written = 0
        lines: list[str] = []
        for row in rows:
            package = str(row.get("package") or "")
            try:
                step = int(row["step"])
            except (KeyError, TypeError, ValueError):
                continue
            verdict = str(row.get("verdict") or "")
            if not package or verdict not in (*VERDICTS, "clear"):
                continue
            reason = str(row.get("reason") or "")
            if reason and reason not in REASONS:
                reason = "other"
            note = str(row.get("note") or "")[:500]
            key = str(row.get("key") or "")
            rule = str(row.get("rule") or "")
            before = int(row.get("before", -1) or -1)
            after = int(row.get("after", -1) or -1)
            payload: dict[str, Any] = {
                "package": package,
                "step": step,
                "before": before,
                "after": after,
                "verdict": verdict,
                "reason": reason,
                "note": note,
                "key": key,
                "reviewer": name,
                "at": stamp,
                "rule": rule,
            }
            lines.append(json.dumps(payload, ensure_ascii=False))
            if verdict == "clear":
                self.verdicts.pop((package, step), None)
            else:
                self.verdicts[(package, step)] = Verdict(
                    package=package,
                    step=step,
                    verdict=verdict,
                    reason=reason,
                    note=note,
                    key=key,
                    reviewer=name,
                    at=stamp,
                    rule=rule,
                    before=before,
                    after=after,
                )
            written += 1
        if lines:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return written

    def undo_rule(self, package: str, rule: str, *, reviewer: str) -> int:
        """Clear every verdict in *package* that a given rule produced.

        The escape hatch for a bulk apply that turned out to be wrong. Only
        verdicts still carrying that ``rule`` are cleared, so a step a human
        re-judged by hand afterwards is left alone.
        """
        rows = [
            {"package": package, "step": step, "verdict": "clear", "rule": rule}
            for step, verdict in sorted(self.for_package(package).items())
            if verdict.rule == rule
        ]
        return self.record(rows, reviewer=reviewer)


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write JSON via a temp file in the same directory, then rename.

    Used for the small derived caches this package keeps under ``review/``.
    A half-written cache read back by the next process is indistinguishable
    from a valid short one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp"
        ) as handle:
            temp = handle.name
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception:  # noqa: BLE001 - a cache is never worth failing over
        logger.debug("could not write cache {}", path)
        if temp:
            Path(temp).unlink(missing_ok=True)


__all__ = [
    "REASONS",
    "VERDICTS",
    "DecisionStore",
    "Verdict",
    "identity_key",
    "sanitize_reviewer",
    "write_json_atomic",
]
