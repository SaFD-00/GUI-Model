#!/usr/bin/env python3
"""Train/Test Split for GUI-Model datasets.

Stage 1 (World Modeling): Random split
Stage 2 (Action Prediction): Stratified split by action type

Usage:
    python scripts/split_data.py --dataset MobiBench
    python scripts/split_data.py --dataset AndroidControl
    python scripts/split_data.py --dataset AndroidControl --ratio 0.9 --seed 123
"""

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path


def split_stage1(entries: list, ratio: float, seed: int) -> tuple[list, list]:
    """Random split for Stage 1 (World Modeling)."""
    random.seed(seed)
    shuffled = entries.copy()
    random.shuffle(shuffled)
    split_idx = int(len(shuffled) * ratio)
    return shuffled[:split_idx], shuffled[split_idx:]


def split_stage2(entries: list, ratio: float, seed: int) -> tuple[list, list]:
    """Stratified split by action type for Stage 2 (Action Prediction)."""
    random.seed(seed)

    type_groups = defaultdict(list)
    for entry in entries:
        action = json.loads(entry["messages"][-1]["value"])
        action_type = action.get("type", "unknown")
        type_groups[action_type].append(entry)

    train_entries, test_entries = [], []
    for action_type, group in type_groups.items():
        random.shuffle(group)
        split_idx = max(1, int(len(group) * ratio))
        train_entries.extend(group[:split_idx])
        test_entries.extend(group[split_idx:])

    random.shuffle(train_entries)
    random.shuffle(test_entries)
    return train_entries, test_entries


def write_jsonl(entries: list, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def print_stage2_distribution(entries: list, label: str) -> None:
    action_types = []
    for entry in entries:
        try:
            action = json.loads(entry["messages"][-1]["value"])
            action_types.append(action.get("type", "unknown"))
        except (json.JSONDecodeError, KeyError):
            action_types.append("parse_error")

    counts = Counter(action_types)
    total = len(action_types)
    print(f"  {label} action type distribution:")
    for atype, count in counts.most_common():
        print(f"    {atype}: {count} ({count / total:.1%})")


def main():
    parser = argparse.ArgumentParser(
        description="Train/Test Split for GUI-Model datasets"
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Dataset name (e.g., MobiBench, AndroidControl)",
    )
    parser.add_argument(
        "--ratio",
        type=float,
        default=0.95,
        help="Train ratio (default: 0.95)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed (default: 42)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Data root directory (default: ./data relative to project root)",
    )
    args = parser.parse_args()

    # Resolve data directory
    if args.data_dir:
        data_root = Path(args.data_dir)
    else:
        # Default: ./data relative to this script's parent (project root)
        data_root = Path(__file__).resolve().parent.parent / "data"

    dataset_dir = data_root / args.dataset
    if not dataset_dir.exists():
        print(f"[ERROR] Dataset directory not found: {dataset_dir}")
        return 1

    print(f"Dataset: {args.dataset}")
    print(f"Directory: {dataset_dir}")
    print(f"Ratio: {args.ratio:.2f} train / {1 - args.ratio:.2f} test")
    print(f"Seed: {args.seed}")
    print()

    # === Stage 1: World Modeling (Random Split) ===
    stage1_path = dataset_dir / "gui-model_stage1.jsonl"
    if stage1_path.exists():
        stage1_entries = []
        skipped = 0
        with open(stage1_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    stage1_entries.append(json.loads(line))
                except json.JSONDecodeError:
                    skipped += 1
        if skipped:
            print(f"  [WARN] Skipped {skipped} malformed lines in Stage 1")

        train, test = split_stage1(stage1_entries, args.ratio, args.seed)
        train_path = dataset_dir / "gui-model_stage1_train.jsonl"
        test_path = dataset_dir / "gui-model_stage1_test.jsonl"
        write_jsonl(train, train_path)
        write_jsonl(test, test_path)

        print(f"=== Stage 1 (World Modeling) ===")
        print(f"  Total: {len(stage1_entries)}")
        print(f"  Train: {len(train)} ({len(train) / len(stage1_entries):.1%})")
        print(f"  Test:  {len(test)} ({len(test) / len(stage1_entries):.1%})")
        print(f"  → {train_path.name}")
        print(f"  → {test_path.name}")
        print()
    else:
        print(f"[SKIP] Stage 1 file not found: {stage1_path}")

    # === Stage 2: Action Prediction (Stratified Split) ===
    stage2_path = dataset_dir / "gui-model_stage2.jsonl"
    if stage2_path.exists():
        stage2_entries = []
        skipped = 0
        with open(stage2_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    stage2_entries.append(json.loads(line))
                except json.JSONDecodeError:
                    skipped += 1
        if skipped:
            print(f"  [WARN] Skipped {skipped} malformed lines in Stage 2")

        train, test = split_stage2(stage2_entries, args.ratio, args.seed)
        train_path = dataset_dir / "gui-model_stage2_train.jsonl"
        test_path = dataset_dir / "gui-model_stage2_test.jsonl"
        write_jsonl(train, train_path)
        write_jsonl(test, test_path)

        print(f"=== Stage 2 (Action Prediction, Stratified) ===")
        print(f"  Total: {len(stage2_entries)}")
        print(f"  Train: {len(train)} ({len(train) / len(stage2_entries):.1%})")
        print(f"  Test:  {len(test)} ({len(test) / len(stage2_entries):.1%})")
        print_stage2_distribution(train, "Train")
        print_stage2_distribution(test, "Test")
        print(f"  → {train_path.name}")
        print(f"  → {test_path.name}")
        print()
    else:
        print(f"[SKIP] Stage 2 file not found: {stage2_path}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
