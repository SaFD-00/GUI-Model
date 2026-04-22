#!/usr/bin/env python3
"""Train / Test split builder for GUI-Model datasets.

학습 대상 DS 는 {AC (AndroidControl), MC (MonkeyCollection)} 만 지원한다.
MobiBench(MB) 는 평가 전용 벤치마크이므로 split 하지 않는다 —
``data/MobiBench/gui-model_stage{1,2}.jsonl`` 두 단일 파일이 eval 입력.

Stage 1 (World Modeling):  random split over ``gui-model_stage1.jsonl``.
Stage 2 (Action Prediction): app-level in-domain / out-of-domain split over
    ``gui-model_stage2.jsonl`` using ``episodes_meta.jsonl`` (primary_app).
    MC 는 Stage 2 데이터가 없으므로 자동 skip (``--skip-stage2`` 기본 적용).

Usage
-----
  # AC: Stage 1 + Stage 2 (defaults: train=50000, test_id=3000, test_ood=3000)
  python scripts/split_data.py --dataset AndroidControl

  # MC: Stage 1 random split 만 수행 (Stage 2 없음)
  python scripts/split_data.py --dataset MonkeyCollection

  # Skip Stage 1 / Stage 2 selectively
  python scripts/split_data.py --dataset AndroidControl --skip-stage1
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


DATASET_DIRS = {
    "AndroidControl": "AndroidControl",
    "AC": "AndroidControl",
    "MonkeyCollection": "MonkeyCollection",
    "MC": "MonkeyCollection",
}

# Stage 2 분할을 지원하지 않는 데이터셋 (Stage 1 전용).
_STAGE1_ONLY = {"MonkeyCollection", "MC"}

EPISODE_RE = re.compile(r"episode_(\d+)")


# ── IO helpers ────────────────────────────────────────────────────────────
def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_jsonl(entries: list, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Stage 1 ───────────────────────────────────────────────────────────────
def split_stage1(entries: list, ratio: float, seed: int) -> tuple[list, list]:
    """Random split for Stage 1 (World Modeling)."""
    rng = random.Random(seed)
    shuffled = entries.copy()
    rng.shuffle(shuffled)
    split_idx = int(len(shuffled) * ratio)
    return shuffled[:split_idx], shuffled[split_idx:]


# ── Stage 2 ───────────────────────────────────────────────────────────────
def stratified_subsample(entries: list, target_size: int, seed: int) -> list:
    """Subsample preserving action-type ratio via largest-remainder method."""
    rng = random.Random(seed)
    type_groups = defaultdict(list)
    for entry in entries:
        action = json.loads(entry["messages"][-1]["value"])
        type_groups[action.get("type", "unknown")].append(entry)

    total = len(entries)
    if target_size >= total:
        return entries.copy()

    quotas: dict[str, int] = {}
    remainders: dict[str, float] = {}
    for atype, group in type_groups.items():
        exact = len(group) / total * target_size
        quotas[atype] = int(exact)
        remainders[atype] = exact - int(exact)

    leftover = target_size - sum(quotas.values())
    for atype, _ in sorted(remainders.items(), key=lambda kv: -kv[1])[:leftover]:
        quotas[atype] += 1

    sampled: list = []
    for atype, group in type_groups.items():
        rng.shuffle(group)
        sampled.extend(group[: quotas[atype]])
    return sampled


def split_stage2_by_ratio(entries: list, ratio: float, seed: int) -> tuple[list, list]:
    """Stratified random split (no ID/OOD) — retained for legacy callers."""
    rng = random.Random(seed)
    type_groups = defaultdict(list)
    for entry in entries:
        action = json.loads(entry["messages"][-1]["value"])
        type_groups[action.get("type", "unknown")].append(entry)

    train_entries, test_entries = [], []
    for action_type, group in type_groups.items():
        rng.shuffle(group)
        split_idx = max(1, int(len(group) * ratio))
        train_entries.extend(group[:split_idx])
        test_entries.extend(group[split_idx:])

    rng.shuffle(train_entries)
    rng.shuffle(test_entries)
    return train_entries, test_entries


def episode_id_from_entry(entry: dict) -> str | None:
    images = entry.get("images") or []
    if not images:
        return None
    m = EPISODE_RE.search(str(images[0]))
    return m.group(1) if m else None


def _norm_ep(raw: object) -> str:
    """Strip leading zeros so '006881' and 6881 both key as '6881'."""
    s = str(raw).strip()
    try:
        return str(int(s))
    except ValueError:
        return s


def partition_apps(
    app_to_rows: dict[str, list[dict]],
    ood_budget: int,
    id_budget: int,
    rng: random.Random,
) -> tuple[list[str], list[str]]:
    """OOD-first app partition so the test_ood pool is always feasible.

    Apps are shuffled then appended to the OOD bucket until ``ood_budget`` rows
    are reached; remaining apps form the in-domain bucket. Warnings are emitted
    if either bucket is under-budget so the caller can adjust sizes.
    """
    apps = list(app_to_rows.keys())
    rng.shuffle(apps)

    ood_apps: list[str] = []
    ood_rows = 0
    idx = 0
    while ood_rows < ood_budget and idx < len(apps):
        a = apps[idx]
        ood_apps.append(a)
        ood_rows += len(app_to_rows[a])
        idx += 1
    id_apps = apps[idx:]

    if ood_rows < ood_budget:
        print(
            f"[warn] OOD pool has {ood_rows} rows (< ood_budget={ood_budget}). "
            "Consider lowering --stage2-test-ood-size or labeling more episodes.",
            file=sys.stderr,
        )
    id_rows = sum(len(app_to_rows[a]) for a in id_apps)
    if id_rows < id_budget:
        print(
            f"[warn] IN-DOMAIN pool has {id_rows} rows (< id_budget={id_budget}). "
            "train/test_id will be smaller than requested.",
            file=sys.stderr,
        )
    return id_apps, ood_apps


def build_stage2_id_ood_split(
    stage2_entries: list[dict],
    meta_entries: list[dict],
    train_size: int,
    test_id_size: int,
    test_ood_size: int,
    seed: int,
    exclude_null_app: bool = False,
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Core ID/OOD splitter. Returns (train, test_id, test_ood, info)."""
    ep_to_app: dict[str, str | None] = {}
    for m in meta_entries:
        ep_to_app[_norm_ep(m.get("episode_id"))] = m.get("primary_app")

    null_rows: list[dict] = []
    app_to_rows: dict[str, list[dict]] = defaultdict(list)
    for entry in stage2_entries:
        ep = episode_id_from_entry(entry)
        if ep is None:
            null_rows.append(entry)
            continue
        app = ep_to_app.get(_norm_ep(ep))
        if app is None or not str(app).strip():
            null_rows.append(entry)
            continue
        app_to_rows[str(app).strip()].append(entry)

    rng = random.Random(seed)
    id_apps, ood_apps = partition_apps(
        app_to_rows,
        ood_budget=test_ood_size,
        id_budget=test_id_size * 2,
        rng=rng,
    )

    id_pool: list[dict] = []
    for a in id_apps:
        id_pool.extend(app_to_rows[a])
    ood_pool: list[dict] = []
    for a in ood_apps:
        ood_pool.extend(app_to_rows[a])

    test_id = stratified_subsample(id_pool, test_id_size, seed + 1)
    marks = {id(e) for e in test_id}
    id_remaining = [e for e in id_pool if id(e) not in marks]

    train_pool = list(id_remaining)
    if not exclude_null_app:
        train_pool.extend(null_rows)
    train = stratified_subsample(train_pool, train_size, seed)

    test_ood = stratified_subsample(ood_pool, test_ood_size, seed + 2)

    info = {
        "total_rows": len(stage2_entries),
        "labeled_rows": sum(len(v) for v in app_to_rows.values()),
        "null_rows": len(null_rows),
        "unique_labeled_apps": len(app_to_rows),
        "id_apps": len(id_apps),
        "ood_apps": len(ood_apps),
        "id_pool_rows": len(id_pool),
        "ood_pool_rows": len(ood_pool),
    }
    return train, test_id, test_ood, info


# ── Reporting ─────────────────────────────────────────────────────────────
def print_stage2_distribution(entries: list, label: str) -> None:
    action_types: list[str] = []
    for entry in entries:
        try:
            action = json.loads(entry["messages"][-1]["value"])
            action_types.append(action.get("type", "unknown"))
        except (json.JSONDecodeError, KeyError):
            action_types.append("parse_error")

    counts = Counter(action_types)
    total = len(action_types)
    print(f"  {label} ({total}):")
    if total == 0:
        return
    for atype, count in counts.most_common():
        print(f"    {atype}: {count} ({count / total:.1%})")


# ── CLI ───────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train/Test split builder (Stage 1 random + Stage 2 ID/OOD)",
    )
    parser.add_argument(
        "--dataset", required=True, choices=sorted(DATASET_DIRS),
        help="Dataset short or full name",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Data root (default: <repo>/data)",
    )
    parser.add_argument("--seed", type=int, default=42)

    parser.add_argument("--skip-stage1", action="store_true")
    parser.add_argument("--skip-stage2", action="store_true")

    parser.add_argument(
        "--stage1-ratio", type=float, default=0.95,
        help="Train ratio for Stage 1 random split (default: 0.95)",
    )

    parser.add_argument("--stage2-train-size", type=int, default=50000)
    parser.add_argument("--stage2-test-id-size", type=int, default=3000)
    parser.add_argument("--stage2-test-ood-size", type=int, default=3000)
    parser.add_argument(
        "--stage2-exclude-null-app", action="store_true",
        help="Drop episodes with null primary_app instead of pooling them into train.",
    )

    args = parser.parse_args()

    if args.dataset in _STAGE1_ONLY and not args.skip_stage2:
        print(f"[info] {args.dataset} 은 Stage 1 전용입니다. Stage 2 는 자동 skip.")
        args.skip_stage2 = True

    ds_dir_name = DATASET_DIRS[args.dataset]
    if args.data_dir:
        data_root = Path(args.data_dir)
    else:
        data_root = Path(__file__).resolve().parent.parent / "data"
    dataset_dir = data_root / ds_dir_name
    if not dataset_dir.exists():
        print(f"[ERROR] Dataset directory not found: {dataset_dir}", file=sys.stderr)
        return 1

    print(f"Dataset: {args.dataset} ({dataset_dir})")
    print(f"Seed: {args.seed}")
    print()

    # ── Stage 1 ───────────────────────────────────────────────────────
    stage1_path = dataset_dir / "gui-model_stage1.jsonl"
    if args.skip_stage1:
        print("[skip] Stage 1 split (per --skip-stage1)")
    elif not stage1_path.exists():
        print(f"[skip] Stage 1 file not found: {stage1_path}")
    else:
        stage1_entries = load_jsonl(stage1_path)
        train, test = split_stage1(stage1_entries, args.stage1_ratio, args.seed)
        train_path = dataset_dir / "gui-model_stage1_train.jsonl"
        test_path = dataset_dir / "gui-model_stage1_test.jsonl"
        write_jsonl(train, train_path)
        write_jsonl(test, test_path)
        print("=== Stage 1 (World Modeling, random) ===")
        print(f"  Total: {len(stage1_entries)}")
        print(f"  Train: {len(train)} ({len(train) / max(len(stage1_entries), 1):.1%})")
        print(f"  Test:  {len(test)} ({len(test) / max(len(stage1_entries), 1):.1%})")
        print(f"  → {train_path.name}")
        print(f"  → {test_path.name}")
        print()

    # ── Stage 2 (ID/OOD) ──────────────────────────────────────────────
    stage2_path = dataset_dir / "gui-model_stage2.jsonl"
    meta_path = dataset_dir / "episodes_meta.jsonl"
    if args.skip_stage2:
        print("[skip] Stage 2 split (per --skip-stage2)")
    elif not stage2_path.exists():
        print(f"[skip] Stage 2 file not found: {stage2_path}")
    elif not meta_path.exists():
        print(f"[ERROR] Stage 2 split requires {meta_path} — run the metadata "
              f"extractor first (extract_{ds_dir_name.lower()}_metadata.py).",
              file=sys.stderr)
        return 1
    else:
        stage2_entries = load_jsonl(stage2_path)
        meta_entries = load_jsonl(meta_path)
        train, test_id, test_ood, info = build_stage2_id_ood_split(
            stage2_entries,
            meta_entries,
            train_size=args.stage2_train_size,
            test_id_size=args.stage2_test_id_size,
            test_ood_size=args.stage2_test_ood_size,
            seed=args.seed,
            exclude_null_app=args.stage2_exclude_null_app,
        )

        train_path = dataset_dir / "gui-model_stage2_train.jsonl"
        test_id_path = dataset_dir / "gui-model_stage2_test_id.jsonl"
        test_ood_path = dataset_dir / "gui-model_stage2_test_ood.jsonl"
        write_jsonl(train, train_path)
        write_jsonl(test_id, test_id_path)
        write_jsonl(test_ood, test_ood_path)

        print("=== Stage 2 (Action Prediction, ID/OOD) ===")
        print(f"  Total rows: {info['total_rows']} "
              f"(labeled {info['labeled_rows']}, null {info['null_rows']})")
        print(f"  Unique labeled apps: {info['unique_labeled_apps']}")
        print(f"  IN-DOMAIN apps:  {info['id_apps']} "
              f"(pool: {info['id_pool_rows']} rows)")
        print(f"  OUT-OF-DOMAIN apps: {info['ood_apps']} "
              f"(pool: {info['ood_pool_rows']} rows)")
        print(f"  → {train_path.name} ({len(train)})")
        print(f"  → {test_id_path.name} ({len(test_id)})")
        print(f"  → {test_ood_path.name} ({len(test_ood)})")
        print_stage2_distribution(train, "train")
        print_stage2_distribution(test_id, "test_id")
        print_stage2_distribution(test_ood, "test_ood")
        print()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
