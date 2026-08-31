"""Human filtering of a collected corpus: verdicts, bulk rules, and a local UI.

The collector cannot tell a screen that is worth learning from one that is not.
It records recoveries, stuck loops, half-drawn screens and screens of other apps
because refusing them at collection time would mean deciding, on the device,
what the dataset is. This package is where that decision is made afterwards, by
a person, and where it is made to BITE: :mod:`monkey_collector.export` reads
these verdicts and leaves excluded triples out of ``stage1_*.jsonl``.

Three modules, one direction of dependency::

    store    verdicts on disk (the only writer in this package)
    corpus   a read-only view of raw/, encoded exactly as the export encodes it
    rules    bulk predicates over what the export would SHIP
    server   the local web UI over the three above

READ-ONLY, BECAUSE A COLLECTION IS PROBABLY RUNNING
===================================================

Reviewing the apps that finished while the sweep works through the rest is the
normal case, not an edge one. Nothing here writes, moves or deletes anything
under ``raw/`` or ``runtime/``; verdicts and caches go to ``review/``, which
:func:`monkey_collector.paths.review_root` owns and ``reset`` deliberately does
not delete.

EXCLUDING IS NEVER DELETING
===========================

A verdict of ``exclude`` removes a record from the next export. It does not
touch the collected bytes, and there is no code path in this package that
deletes an observation. Re-collecting an app costs two hours on a real device;
a wrong verdict must cost nothing but a keystroke to undo.
"""

from __future__ import annotations

__all__ = ["corpus", "rules", "server", "store"]
