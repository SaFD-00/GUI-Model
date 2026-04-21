#!/usr/bin/env python3
"""
Extract MobiBench per-episode metadata — episode_id, goal (task), primary_app,
step count — into a JSONL usable by build_stage2_splits.py for in-domain /
out-of-domain splitting.

MobiBench does not ship a TFRecord source like AndroidControl; episodes are
reconstructed by grouping rows of ``gui-model_stage2.jsonl`` by the
``episode_XXXXXX`` prefix embedded in each ``images`` path.

primary_app resolution order:
  1. First ``OpenApp`` action's ``params.app`` (rare in MobiBench but
     authoritative when present).
  2. Regex match ``<Title-Cased Words> app`` in the earliest step's task
     description (e.g. "using Audio Recorder app" -> "Audio Recorder").
  3. None (episode is excluded from ID/OOD splits downstream).

Usage:
    python scripts/extract_mobibench_metadata.py \
        --input  data/MobiBench/gui-model_stage2.jsonl \
        --output data/MobiBench/episodes_meta.jsonl
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

EPISODE_RE = re.compile(r"episode_(\d+)_step_(\d+)")

# Matches "<Capitalized Words (1-4)> app" (case-sensitive 'app' word).
# Examples matched: "Audio Recorder app", "Broccoli app", "Zoho Meet app".
APP_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3})\s+app\b"
)

# Common sentence-initial capitalized words that get swept up by APP_RE when
# they precede an app name (e.g. "In Google Dialer app"). Stripped from the
# left of the captured phrase during normalization.
LEADING_STOPWORDS = {"In", "On", "The", "A", "An", "Using", "From", "Open",
                     "Launch", "With"}


def _clean_app(raw: str | None) -> str | None:
    if not raw:
        return None
    tokens = raw.strip().split()
    while tokens and tokens[0] in LEADING_STOPWORDS:
        tokens = tokens[1:]
    if not tokens:
        return None
    return " ".join(tokens)


def parse_episode_step(image_path: str) -> tuple[str, int] | None:
    m = EPISODE_RE.search(image_path or "")
    if not m:
        return None
    return m.group(1), int(m.group(2))


def extract_task_from_human(human_value: str) -> str | None:
    """Return the text between '## Task' and '## Current UI State' markers."""
    if not human_value:
        return None
    m = re.search(r"##\s*Task\s*\n(.*?)(?:\n\s*##|\Z)", human_value, re.DOTALL)
    if not m:
        return None
    return m.group(1).strip()


def primary_app_from_action(action: dict) -> str | None:
    if not isinstance(action, dict):
        return None
    if str(action.get("type", "")).lower() != "openapp":
        return None
    params = action.get("params") or {}
    app = params.get("app") if isinstance(params, dict) else None
    if isinstance(app, str) and app.strip():
        return _clean_app(app)
    return None


def primary_app_from_task(task: str) -> str | None:
    if not task:
        return None
    m = APP_RE.search(task)
    if not m:
        return None
    return _clean_app(m.group(1))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract MobiBench per-episode metadata to JSONL.",
    )
    ap.add_argument(
        "--input",
        default="data/MobiBench/gui-model_stage2.jsonl",
        help="Input Stage 2 JSONL (default: data/MobiBench/gui-model_stage2.jsonl)",
    )
    ap.add_argument(
        "--output",
        default="data/MobiBench/episodes_meta.jsonl",
        help="Output JSONL path",
    )
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 1

    # episode_id -> list of (step, human_value, gpt_action)
    rows_by_ep: dict[str, list[tuple[int, str, dict | None]]] = defaultdict(list)

    with open(args.input, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            try:
                entry = json.loads(raw)
            except Exception as e:
                print(f"[warn] line {line_no}: JSON parse failed: {e}", file=sys.stderr)
                continue
            images = entry.get("images") or []
            if not images:
                continue
            parsed = parse_episode_step(images[0])
            if not parsed:
                continue
            ep_id, step = parsed
            human_value = ""
            gpt_action: dict | None = None
            for msg in entry.get("messages", []):
                role = msg.get("from")
                if role == "human" and not human_value:
                    human_value = msg.get("value") or ""
                elif role == "gpt" and gpt_action is None:
                    try:
                        gpt_action = json.loads(msg.get("value") or "")
                    except Exception:
                        gpt_action = None
            rows_by_ep[ep_id].append((step, human_value, gpt_action))

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    n_written = 0
    n_null = 0
    with open(args.output, "w", encoding="utf-8") as fout:
        for ep_id in sorted(rows_by_ep.keys()):
            rows = sorted(rows_by_ep[ep_id], key=lambda t: t[0])
            first_human = rows[0][1]
            task = extract_task_from_human(first_human)

            primary_app: str | None = None
            for _step, _h, action in rows:
                primary_app = primary_app_from_action(action)
                if primary_app:
                    break
            if not primary_app:
                primary_app = primary_app_from_task(task or "")

            record = {
                "episode_id": ep_id,
                "goal": task,
                "primary_app": primary_app,
                "n_steps": len(rows),
            }
            fout.write(json.dumps(record, ensure_ascii=False))
            fout.write("\n")
            n_written += 1
            if primary_app is None:
                n_null += 1
            if args.verbose:
                print(f"  episode {ep_id}: primary_app={primary_app!r}, steps={len(rows)}")

    print(f"Episodes written: {n_written}")
    print(f"Episodes with null primary_app: {n_null}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
