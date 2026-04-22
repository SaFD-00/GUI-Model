#!/usr/bin/env python3
"""
One-off migration: normalize JSONL `images` field paths to JSONL-relative form.

MobiBench:       GUI-Model/images/episode_{id:06d}_step_{idx:04d}.png
              -> images/episode_{id:06d}_step_{idx:04d}.png

MobiBench:       MobiBench/images/episode_{id:06d}_step_{idx:04d}.png
              -> images/episode_{id:06d}_step_{idx:04d}.png

AndroidControl:  myset/images/episode_{id}_step_{idx}.png (no padding)
              -> images/episode_{id:06d}_step_{idx:04d}.png

AndroidControl:  AndroidControl/images/episode_{id:06d}_step_{idx:04d}.png
              -> images/episode_{id:06d}_step_{idx:04d}.png

MonkeyCollection: MonkeyCollection/images/...  또는 prefix 만 다른 변형
              -> images/...

Usage:
    python scripts/fix_jsonl_image_paths.py --dry-run
    python scripts/fix_jsonl_image_paths.py
    python scripts/fix_jsonl_image_paths.py --dataset MobiBench
    python scripts/fix_jsonl_image_paths.py --dataset MonkeyCollection
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

RELATIVE_PREFIX = "images/"
MB_PREFIXES = ("GUI-Model/images/", "MobiBench/images/")

AC_PATTERN = re.compile(r"^myset/images/episode_(\d+)_step_(\d+)\.png$")
AC_PREFIX = "AndroidControl/images/"

MC_PREFIXES = ("MonkeyCollection/images/",)

JSONL_FILES = [
    "gui-model_stage1.jsonl",
    "gui-model_stage1_train.jsonl",
    "gui-model_stage1_test.jsonl",
    "gui-model_stage2.jsonl",
    "gui-model_stage2_train.jsonl",
    "gui-model_stage2_test.jsonl",
]


def convert_mb(path: str) -> tuple[str, str]:
    if path.startswith(RELATIVE_PREFIX):
        return path, "already"
    for prefix in MB_PREFIXES:
        if path.startswith(prefix):
            return RELATIVE_PREFIX + path[len(prefix):], "converted"
    return path, "unmatched"


def convert_ac(path: str) -> tuple[str, str]:
    m = AC_PATTERN.match(path)
    if m:
        eid, sidx = int(m.group(1)), int(m.group(2))
        return f"{RELATIVE_PREFIX}episode_{eid:06d}_step_{sidx:04d}.png", "converted"
    if path.startswith(RELATIVE_PREFIX):
        return path, "already"
    if path.startswith(AC_PREFIX):
        return RELATIVE_PREFIX + path[len(AC_PREFIX):], "converted"
    return path, "unmatched"


def convert_mc(path: str) -> tuple[str, str]:
    if path.startswith(RELATIVE_PREFIX):
        return path, "already"
    for prefix in MC_PREFIXES:
        if path.startswith(prefix):
            return RELATIVE_PREFIX + path[len(prefix):], "converted"
    return path, "unmatched"


def process_file(jsonl_path: Path, converter, dry_run: bool) -> dict:
    stats = {"lines": 0, "converted": 0, "already": 0, "unmatched": 0}
    samples = []
    out_lines = []

    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                out_lines.append(line)
                continue
            stats["lines"] += 1
            obj = json.loads(line)
            images = obj.get("images") or []
            new_images = []
            line_changed = False
            for p in images:
                new_p, status = converter(p)
                stats[status] += 1
                if new_p != p:
                    line_changed = True
                new_images.append(new_p)
                if len(samples) < 3 and status == "converted":
                    samples.append((p, new_p))
            if line_changed:
                obj["images"] = new_images
            out_lines.append(json.dumps(obj, ensure_ascii=False))

    if not dry_run and stats["converted"] > 0:
        with jsonl_path.open("w", encoding="utf-8") as f:
            f.write("\n".join(out_lines))
            if out_lines:
                f.write("\n")

    return {"stats": stats, "samples": samples}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dataset",
        choices=["MobiBench", "AndroidControl", "MonkeyCollection", "all"],
        default="all",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = []
    if args.dataset in ("MobiBench", "all"):
        targets.append(("MobiBench", convert_mb))
    if args.dataset in ("AndroidControl", "all"):
        targets.append(("AndroidControl", convert_ac))
    if args.dataset in ("MonkeyCollection", "all"):
        targets.append(("MonkeyCollection", convert_mc))

    mode = "DRY-RUN" if args.dry_run else "WRITE"
    print(f"[{mode}] data dir: {REPO_DATA_DIR}")
    print()

    exit_code = 0
    for ds_name, converter in targets:
        ds_dir = REPO_DATA_DIR / ds_name
        print(f"=== {ds_name} ({ds_dir}) ===")
        if not ds_dir.is_dir():
            print(f"  [SKIP] directory not found")
            exit_code = 1
            continue

        for fname in JSONL_FILES:
            fpath = ds_dir / fname
            if not fpath.is_file():
                print(f"  [SKIP] {fname} (not found)")
                continue

            result = process_file(fpath, converter, args.dry_run)
            s = result["stats"]
            print(
                f"  {fname}: {s['lines']} lines, "
                f"converted={s['converted']}, already={s['already']}, unmatched={s['unmatched']}"
            )
            if s["unmatched"] > 0:
                print(f"    [WARN] {s['unmatched']} path(s) did not match expected pattern")
                exit_code = 2
            if args.dry_run and result["samples"]:
                for old, new in result["samples"][:2]:
                    print(f"    sample: {old}")
                    print(f"         -> {new}")
        print()

    print("Done.")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
