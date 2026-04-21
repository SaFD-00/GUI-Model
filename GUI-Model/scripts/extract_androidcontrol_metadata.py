#!/usr/bin/env python3
"""
Extract AndroidControl per-episode metadata (goal, step_instructions, actions,
app-level fields, ...) from GCS TFRecord files into a single JSONL, so it can be
joined with gui-model_stage{1,2}.jsonl by episode_id.

Screenshots are already extracted by extract_androidcontrol_images.py and are
skipped here.

Usage:
    python scripts/extract_androidcontrol_metadata.py \
        --output data/AndroidControl/episodes_meta.jsonl --verbose

    # Inspect a few episodes first to see which feature keys carry app info:
    python scripts/extract_androidcontrol_metadata.py \
        --output data/AndroidControl/episodes_meta.jsonl \
        --max-episodes 3 --verbose
"""

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error

from extract_androidcontrol_images import (
    GCS_BUCKET,
    GCS_PREFIX,
    gcs_download_to_file,
    gcs_list_objects,
    iter_tfrecord_gzip,
    parse_example,
)

# Per-step binary/huge features we never want to emit (screenshots: PNG data;
# accessibility_trees: serialized proto that's useless as utf-8 text).
SKIP_FEATURES = {"screenshots", "accessibility_trees"}

# Drop any single bytes entry larger than this (defense in depth for stray blobs).
MAX_BYTES_PER_ENTRY = 64 * 1024


def feature_to_jsonable(feat: tuple[str, list]) -> object | None:
    kind, values = feat
    if kind == "int64_list":
        return values[0] if len(values) == 1 else values
    if kind == "bytes_list":
        decoded = []
        for v in values:
            if len(v) > MAX_BYTES_PER_ENTRY:
                decoded.append(f"<{len(v)} bytes omitted>")
            else:
                decoded.append(v.decode("utf-8", errors="replace"))
        return decoded[0] if len(decoded) == 1 else decoded
    return None


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract AndroidControl per-episode metadata to JSONL."
    )
    ap.add_argument(
        "--output",
        default="data/AndroidControl/episodes_meta.jsonl",
        help="Output JSONL path (default: data/AndroidControl/episodes_meta.jsonl)",
    )
    ap.add_argument(
        "--max-episodes",
        type=int,
        default=0,
        help="Limit to N episodes (0 = unlimited)",
    )
    ap.add_argument("--verbose", action="store_true", help="Per-episode logging")
    args = ap.parse_args()

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)

    print(f"Output: {args.output}")
    if args.max_episodes > 0:
        print(f"Max episodes: {args.max_episodes}")

    print("Listing TFRecord files from GCS...")
    try:
        obj_names = gcs_list_objects(GCS_BUCKET, GCS_PREFIX)
    except urllib.error.URLError as e:
        print(f"ERROR: Failed to list GCS objects: {e}", file=sys.stderr)
        sys.exit(1)
    if not obj_names:
        print("ERROR: No TFRecord files found in GCS bucket", file=sys.stderr)
        sys.exit(1)
    print(f"Found {len(obj_names)} TFRecord files\n")

    t_start = time.time()
    total_episodes = 0
    total_errors = 0
    seen_keys: set[str] = set()
    done = False

    with open(args.output, "w", encoding="utf-8") as fout:
        for file_idx, obj_name in enumerate(obj_names):
            if done:
                break

            file_name = os.path.basename(obj_name)
            print(f"[{file_idx + 1}/{len(obj_names)}] Downloading {file_name} ...")

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tfrecord.gz")
            os.close(tmp_fd)

            try:
                gcs_download_to_file(GCS_BUCKET, obj_name, tmp_path)
                size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
                print(f"  Downloaded {size_mb:.1f} MB, parsing...")

                file_episodes = 0
                for record_data in iter_tfrecord_gzip(tmp_path):
                    try:
                        features = parse_example(record_data)
                    except Exception as e:
                        total_errors += 1
                        if args.verbose:
                            print(f"  [WARN] parse failed: {e}")
                        continue

                    if "episode_id" not in features:
                        total_errors += 1
                        continue

                    record: dict[str, object] = {}
                    for name, feat in features.items():
                        if name in SKIP_FEATURES:
                            continue
                        val = feature_to_jsonable(feat)
                        if val is not None:
                            record[name] = val
                        seen_keys.add(name)

                    fout.write(json.dumps(record, ensure_ascii=False))
                    fout.write("\n")
                    file_episodes += 1
                    total_episodes += 1

                    if args.verbose:
                        print(
                            f"  episode {record.get('episode_id')}: "
                            f"keys={sorted(record.keys())}"
                        )

                    if args.max_episodes > 0 and total_episodes >= args.max_episodes:
                        done = True
                        break

                elapsed = time.time() - t_start
                print(
                    f"  -> {file_episodes} episodes "
                    f"(cumulative: {total_episodes} episodes, {elapsed:.0f}s)\n"
                )
            except urllib.error.URLError as e:
                print(f"  [ERROR] Download failed: {e}")
                total_errors += 1
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    elapsed = time.time() - t_start
    print("=" * 60)
    print(f"Done! {elapsed:.1f}s elapsed")
    print(f"Episodes written:  {total_episodes}")
    print(f"Errors:            {total_errors}")
    print(f"Feature keys seen: {sorted(seen_keys)}")


if __name__ == "__main__":
    main()
