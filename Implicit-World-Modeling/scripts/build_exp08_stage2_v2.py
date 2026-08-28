#!/usr/bin/env python3
"""AC_EXP08 stage2 재빌드 — train 30K + app/step 축 eval 버킷 7 종.

``scripts/build_exp08_data.py`` (stage1/stage2 정본 빌더) 의 **stage2 부분만** 다시
만드는 후속 빌더다. stage1 산출물 5 개
(``stage1_train.jsonl`` / ``stage1_test_{action,state_full,state_masked,state_dropped}.jsonl``)
는 이미 학습·평가가 끝난 불가침 자산이라 **바이트 불변**이어야 하고, 이 스크립트는
빌드 전후로 sha256 을 대조해 그것을 증명한다 (``_build/stage1_immutable.sha256.json``).

무엇이 왜 바뀌는가
------------------
기존 ``stage2_train.jsonl`` (15,000) 은 stage1 의 **state 40K 와 step 을 8,723 건
공유**한다. 정본 빌더가 stage1-down / stage2 두 표본만 서로 disjoint 하게 뽑았고
state 풀은 고려하지 않았기 때문이다. "학습 샘플이 겹치면 안 된다" 는 요구에 따라
새 train 은 **stage1_train 의 모든 step(state ∪ action)을 제외한 풀 42,683** 에서
뽑는다. stage2 학습 이력이 0 이라 덮어써도 손실이 없다 (이전 파일은 백업한다).

앱 파티션 (``episodes_meta.jsonl::primary_app``)
-----------------------------------------------
  P_ACTION      754  stage1 downstream 10K 이 action 지도를 준 앱
  P_STATE_ONLY   68  나머지 — stage1 이 state 로만 봤거나 아예 못 본 앱
  → APP_S1_ONLY / APP_BOTH  = P_ACTION 을 홀드아웃(기본 60) / 나머지로 분할
  → APP_S2_ONLY / APP_OOD   = P_STATE_ONLY 를 기본 24 / 44 로 분할

**재고 현실**: P_STATE_ONLY 68 앱의 소스 step 은 717 개뿐이다. 그래서
``app_s2_only``·``app_ood`` 버킷은 목표 500 을 채우지 못한다 — 규칙을 완화하지 않고
재고 상한까지만 뽑고 실현 N 을 sidecar 와 로그에 남긴다.

왜 OOD 에피소드를 미리 예약하는가 (없으면 ``step_ood`` 가 조용히 빈다)
----------------------------------------------------------------------
``_episode_roundrobin`` 은 에피소드를 라운드로빈으로 훑으므로 42,683 풀에서 30,000 을
뽑으면 **풀에 있는 거의 모든 에피소드가 train 에 한 번은 들어간다**. "stage1·stage2
어느 에피소드에도 없는 step" 이라는 ``step_ood`` 정의가 사후에는 성립할 수 없다.
그래서 stage1 이 손대지 않은 APP_BOTH 에피소드 중 ``--n-ood-episodes`` 개를 **train
풀에서 먼저 빼둔다.** 앱 축 제한이 아니라 재고 확보용 장치다 (step 축 버킷 자체에는
앱 제한이 없다 — 각 레코드의 ``app_bucket`` 라벨로 사후 교차분석한다).

기존 test 파일 제외
-------------------
``stage1_test_action.jsonl`` 500 + ``stage2_test.jsonl`` 500 은 **전부 새 풀 안에**
있다. 그대로 두면 새 train 이 stage1 action 평가셋을 학습해버린다 (M1 이 그 평가셋으로
13 개 leaf 를 재채점한다). ``stage1_test_state_*`` 500 도 마찬가지다 — 이 500 step 은
stage1_train 에 없으니(구 빌드의 홀드아웃 에피소드) 풀 필터를 그대로 통과하는데,
실측 결과 233 건이 train 에 들어갔다. "state 로 본 화면도 같은 입력" 이라는 제외
근거는 eval 쪽에도 똑같이 적용되고, "stage2 FT 가 world modeling 을 침식하는가" 를
이 파일들로 재는 이상 학습 입력으로 쓰면 안 된다. train 과 7 개 신규 버킷 **양쪽에서**
뺀다 (LEGACY_TESTS).

Usage
-----
  python scripts/build_exp08_stage2_v2.py
  python scripts/build_exp08_stage2_v2.py --n-app-s1-only 40 --n-ood-episodes 300
  python scripts/build_exp08_stage2_v2.py --verify-only

conda env ``implicit-world-modeling`` 의 python 으로 실행한다 (transformers 필요).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

PROJ = Path(__file__).resolve().parent.parent
SCRIPTS = PROJ / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_exp08_data import (  # noqa: E402
    _EP_RE,
    CUTOFF_LEN,
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    DEFAULT_SEED,
    IMG_MAX_PIXELS,
    IMG_MIN_PIXELS,
    _abort,
    _action_of_gpt,
    _read_jsonl,
    _write_jsonl,
    remap_image,
    remap_record,
    sample_stratified,
)

# ── 상수 ─────────────────────────────────────────────────────────────────────
SRC_DOWN = "EXP08_stage2.jsonl"
SRC_META = "episodes_meta.jsonl"
SRC_DEFAULT_SUBDIR = "AndroidControl"
OUT_SUBDIR = "AndroidControl_EXP08"

# stage1 산출물 — 이 빌더는 읽기만 하고, 빌드 전후 sha256 이 같아야 한다.
STAGE1_IMMUTABLE = (
    "stage1_train.jsonl",
    "stage1_test_action.jsonl",
    "stage1_test_state_full.jsonl",
    "stage1_test_state_masked.jsonl",
    "stage1_test_state_dropped.jsonl",
)
# 기존 평가셋 — 새 train·새 버킷 어디에도 들어가면 안 된다. state 3 포맷은 같은 500
# 원본에서 나오므로 (정본 빌더의 불변식) 세 파일을 다 넣어도 step 은 500 개다.
LEGACY_TESTS = (
    "stage1_test_action.jsonl",
    "stage2_test.jsonl",
    "stage1_test_state_full.jsonl",
    "stage1_test_state_masked.jsonl",
    "stage1_test_state_dropped.jsonl",
)

N_S2_TRAIN = 30000
N_BUCKET = 500
N_APP_S1_ONLY = 60
N_APP_S2_ONLY = 24
N_OOD_EPISODES = 250
S2_ONLY_TRAIN_FRAC = 0.5
# 30K 를 뽑고도 step_id_s2 후보로 쓸 잔여분이 남아야 한다. 홀드아웃 앱 수·예약
# 에피소드 수를 올리면 둘이 동시에 줄어드는데, 가드가 없으면 실패가 "빈 버킷" 으로
# 조용히 나타난다.
POOL_HEADROOM = 1500

# 재고가 적은 버킷부터 배정한다 (순차 배정 = 버킷 상호 배타의 보장 수단).
BUCKET_ORDER = (
    "app_ood",
    "app_s2_only",
    "step_ood",
    "app_s1_only",
    "step_id_s2",
    "step_id_s1",
    "app_both",
)
APP_BUCKETS = ("app_both", "app_s1_only", "app_s2_only", "app_ood")
STEP_BUCKETS = ("step_id_s1", "step_id_s2", "step_ood")

_STEP_RE = re.compile(r"_step_(\d+)\.jpg$")


class Step(NamedTuple):
    """downstream 소스 한 행의 메타. 레코드 본문은 ``records[idx]`` 에 따로 둔다."""

    idx: int
    key: str  # remap 된 이미지 경로 = step 의 유일 키
    ep: int
    no: int  # 에피소드 안의 step 번호
    act: str
    app: str | None


# ── 해시 / 불변 자산 ─────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def snapshot_stage1(out_dir: Path) -> dict:
    return {
        n: {
            "sha256": _sha256(out_dir / n),
            "size": (out_dir / n).stat().st_size,
            "mtime_ns": (out_dir / n).stat().st_mtime_ns,
        }
        for n in STAGE1_IMMUTABLE
    }


def compare_stage1(baseline: dict, now: dict, *, label: str) -> list[str]:
    fails = []
    for n in STAGE1_IMMUTABLE:
        b, c = baseline.get(n), now.get(n)
        if b is None:
            fails.append(f"{label}: {n} 기준값 없음")
            continue
        for field in ("sha256", "size", "mtime_ns"):
            if b[field] != c[field]:
                fails.append(f"{label}: {n} {field} 변경 {b[field]} → {c[field]}")
    return fails


# ── 로딩 ─────────────────────────────────────────────────────────────────────


def _step_key(rec: dict) -> str:
    img = rec["images"][0]
    return img if img.startswith("AndroidControl/") else remap_image(img)


def load_stage1_step_keys(path: Path) -> tuple[set[str], set[str]]:
    """stage1_train 을 스트리밍해 (state 40K, action 10K) step 키 집합을 만든다.

    state 레코드는 3-포맷 변환을 거쳐 ``fmt`` 키를 갖고 action 레코드는 갖지 않는다
    (정본 빌더 build() 9 단계).
    """
    state: set[str] = set()
    action: set[str] = set()
    with path.open() as f:
        for line in f:
            rec = json.loads(line)
            (state if "fmt" in rec else action).add(_step_key(rec))
    return state, action


def load_step_keys(path: Path) -> set[str]:
    with path.open() as f:
        return {_step_key(json.loads(line)) for line in f}


def load_downstream(path: Path, ep2app: dict[int, str]) -> tuple[list[dict], list[Step]]:
    records: list[dict] = []
    steps: list[Step] = []
    with path.open() as f:
        for i, line in enumerate(f):
            rec = json.loads(line)
            key = remap_image(rec["images"][0])
            m = _STEP_RE.search(key)
            if not m:
                _abort(f"{path.name}:{i} step 번호 파싱 실패: {key!r}")
            ep = int(_EP_RE.search(key).group(1))
            records.append(rec)
            steps.append(
                Step(i, key, ep, int(m.group(1)), _action_of_gpt(rec, path.name, i),
                     ep2app.get(ep))
            )
    return records, steps


# ── 앱 파티션 ────────────────────────────────────────────────────────────────


def partition_apps(steps, s1_action, rng, *, n_s1_only, n_s2_only) -> dict[str, list[str]]:
    """앱을 4 그룹으로 나눈다. rng 소비 순서가 고정이라 seed 만으로 재현된다."""
    apps_all = sorted({s.app for s in steps if s.app})
    p_action = sorted({s.app for s in steps if s.app and s.key in s1_action})
    p_state_only = sorted(set(apps_all) - set(p_action))
    if n_s2_only > len(p_state_only):
        _abort(f"--n-app-s2-only {n_s2_only} > P_STATE_ONLY {len(p_state_only)}")
    if n_s1_only >= len(p_action):
        _abort(f"--n-app-s1-only {n_s1_only} >= P_ACTION {len(p_action)}")

    so = p_state_only[:]
    rng.shuffle(so)
    pa = p_action[:]
    rng.shuffle(pa)
    return {
        "APP_S2_ONLY": sorted(so[:n_s2_only]),
        "APP_OOD": sorted(so[n_s2_only:]),
        "APP_S1_ONLY": sorted(pa[:n_s1_only]),
        "APP_BOTH": sorted(pa[n_s1_only:]),
    }


# ── 길이 필터 ────────────────────────────────────────────────────────────────


def filter_by_length(pool: list[Step], records, args, media_dir) -> tuple[list[Step], list[Step]]:
    """mm-expanded 길이 > cutoff 인 step 을 train 풀에서 뺀다 (정본 빌더와 같은 이유).

    학습 dataloader 가 잘리기 *전* 의 image_grid_thw 로 위치를 만들어 vision feature
    수와 어긋나 죽는다. eval 버킷에는 걸지 않는다 (정본 빌더도 test 는 무손실 유지).
    """
    if args.skip_length_filter:
        print("[len-filter] skipped (--skip-length-filter)")
        return pool, []
    from filter_long_samples import build_length_fn  # noqa: PLC0415
    from transformers import AutoProcessor  # noqa: PLC0415

    proc = AutoProcessor.from_pretrained(
        args.model, revision=args.revision or None, trust_remote_code=True)
    length_of = build_length_fn(
        proc, image_max_pixels=IMG_MAX_PIXELS, image_min_pixels=IMG_MIN_PIXELS)
    kept, dropped = [], []
    for s in pool:
        length = length_of(remap_record(dict(records[s.idx])), media_dir)
        (dropped if length is None or length > CUTOFF_LEN else kept).append(s)
    print(f"[len-filter] train pool: {len(pool)} → keep {len(kept)}, "
          f"drop {len(dropped)} (>{CUTOFF_LEN})")
    return kept, dropped


# ── 표본 추출 ────────────────────────────────────────────────────────────────


def _take(pool: list[Step], target: int, rng, *, label: str) -> tuple[list[Step], dict]:
    """action 층화 + 에피소드 라운드로빈. 재고가 목표보다 적으면 재고 상한까지."""
    n = min(target, len(pool))
    if n == 0:
        print(f"[sample] {label}: 재고 0 — 빈 버킷")
        return [], {"target": target, "realized_n": 0, "pool": 0, "capped": True}
    sel, meta = sample_stratified(
        [(s, s.act, str(s.ep)) for s in pool], n, rng, label=label)
    meta.update({"pool": len(pool), "realized_n": len(sel), "capped": n < target})
    if n < target:
        print(f"[sample] {label}: 재고 {len(pool)} < 목표 {target} → {n} 만 추출")
    return sel, meta


# ── 라벨 ─────────────────────────────────────────────────────────────────────


def make_labeler(app_bucket_of, s1_state, s1_action, s1_eps, train_keys, train_eps):
    """레코드에 사후 교차분석용 보조 라벨을 붙이는 클로저를 만든다.

    ``step_bucket`` 은 파일 소속과 무관하게 **step 의 노출 이력**으로만 결정한다 —
    app 축 버킷 레코드에도 붙어 두 축의 교차표를 만들 수 있다.
    """

    def step_bucket(s: Step) -> str:
        if s.key in train_keys:
            return "s2_train"
        if s.key in s1_action:
            return "s1_action_train"
        if s.key in s1_state:
            return "step_id_s1"
        if s.ep in train_eps:
            return "step_id_s2"
        if s.ep in s1_eps:
            return "s1_episode_only"
        return "step_ood"

    def label(rec: dict, s: Step, bucket: str) -> dict:
        return {
            **remap_record(dict(rec)),
            "bucket": bucket,
            "primary_app": s.app,
            "app_bucket": app_bucket_of[s.app],
            "step_bucket": step_bucket(s),
            "s1_state_seen": s.key in s1_state,
            "episode_id": s.ep,
            "step_id": s.no,
        }

    return label


# ── 빌드 ─────────────────────────────────────────────────────────────────────


def build(args: argparse.Namespace) -> dict:  # noqa: PLR0915  (선형 파이프라인)
    src_dir = args.source_dir
    out_dir = args.data_root / OUT_SUBDIR
    work = out_dir / "_build"
    work.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    # ── 1. stage1 불가침 자산 기준값 ─────────────────────────────────────────
    base_p = work / "stage1_immutable.sha256.json"
    pre = snapshot_stage1(out_dir)
    if base_p.exists():
        baseline = json.loads(base_p.read_text())
        fails = compare_stage1(baseline, pre, label="pre-build")
        if fails:
            for f in fails:
                print(f"[FAIL] {f}")
            _abort("stage1 산출물이 기준값과 다르다 — 빌드를 중단한다")
        print(f"[stage1] 기준값 {base_p.name} 대조 통과 ({len(STAGE1_IMMUTABLE)} 파일)")
    else:
        base_p.write_text(json.dumps(pre, ensure_ascii=False, indent=2))
        print(f"[stage1] 기준값 신규 기록 → {base_p}")
        baseline = pre

    # ── 2. 로드 ──────────────────────────────────────────────────────────────
    ep2app = {}
    with (src_dir / SRC_META).open() as f:
        for line in f:
            r = json.loads(line)
            ep2app[int(r["episode_id"])] = r.get("primary_app")
    s1_state, s1_action = load_stage1_step_keys(out_dir / "stage1_train.jsonl")
    s1_all = s1_state | s1_action  # 합집합은 루프 밖에서 한 번만
    s1_eps = {int(_EP_RE.search(k).group(1)) for k in s1_all}
    legacy = set()
    for n in LEGACY_TESTS:
        legacy |= load_step_keys(out_dir / n)
    records, steps = load_downstream(src_dir / SRC_DOWN, ep2app)
    print(f"[load] downstream {len(steps)} step / stage1 state {len(s1_state)} + "
          f"action {len(s1_action)} / legacy test {len(legacy)}")

    # ── 3. 앱 파티션 ─────────────────────────────────────────────────────────
    apps = partition_apps(steps, s1_action, rng,
                          n_s1_only=args.n_app_s1_only, n_s2_only=args.n_app_s2_only)
    app_bucket_of = {a: g for g, lst in apps.items() for a in lst}
    # 집합은 루프 밖에서 한 번만 만든다 (86,431 행 × 재생성이면 타임아웃 난다).
    aset = {g: set(lst) for g, lst in apps.items()}
    train_apps = aset["APP_BOTH"] | aset["APP_S2_ONLY"]

    # primary_app 이 없는 step(56) 은 앱 파티션이 불가능하다 → 전부에서 제외한다.
    # 기존 평가셋 1,000 step 도 같이 뺀다 (train 오염 + 버킷 중복 방지).
    n_no_app = sum(1 for s in steps if not s.app)
    usable = [s for s in steps if s.app and s.key not in legacy]
    print(f"[filter] usable {len(usable)} (no-app {n_no_app}, legacy-test "
          f"{len(steps) - n_no_app - len(usable)} 제외)")

    # ── 4. step_ood 용 에피소드 예약 ─────────────────────────────────────────
    untouched = sorted({s.ep for s in usable
                        if s.ep not in s1_eps and s.app in aset["APP_BOTH"]})
    if args.n_ood_episodes > len(untouched):
        _abort(f"--n-ood-episodes {args.n_ood_episodes} > 예약 가능 에피소드 {len(untouched)}")
    shuffled = untouched[:]
    rng.shuffle(shuffled)
    reserved_eps = set(shuffled[: args.n_ood_episodes])
    n_reserved_steps = sum(1 for s in usable if s.ep in reserved_eps)
    print(f"[reserve] step_ood 용 에피소드 {len(reserved_eps)}/{len(untouched)} "
          f"→ {n_reserved_steps} step (train 풀에서 제외)")

    # ── 5. train 풀 ──────────────────────────────────────────────────────────
    n_after_s1 = sum(1 for s in usable if s.key not in s1_all)
    n_after_app = sum(1 for s in usable if s.key not in s1_all and s.app in train_apps)
    train_pool = [s for s in usable
                  if s.key not in s1_all and s.app in train_apps and s.ep not in reserved_eps]
    n_after_reserve = len(train_pool)
    train_pool, len_dropped = filter_by_length(train_pool, records, args, args.data_root)

    s2_only_pool = [s for s in train_pool if s.app in aset["APP_S2_ONLY"]]
    both_pool = [s for s in train_pool if s.app in aset["APP_BOTH"]]
    if len(both_pool) < args.n_s2 + POOL_HEADROOM:
        _abort(
            f"APP_BOTH train 풀 {len(both_pool)} < 목표 {args.n_s2} + 여유 {POOL_HEADROOM}. "
            f"내역: usable {len(usable)} → stage1 step 제외 {n_after_s1} → "
            f"APP_S1_ONLY·APP_OOD 앱 제외 {n_after_app} → 예약 에피소드 제외 "
            f"{n_after_reserve} → 길이필터 {len(train_pool)} → APP_BOTH {len(both_pool)}. "
            "--n-app-s1-only 또는 --n-ood-episodes 를 낮춰라"
        )

    # APP_S2_ONLY 앱은 "stage2 가 봤다" 가 성립해야 하므로 각 앱의 풀 step 을 쪼개
    # 일부를 반드시 train 에 넣고 나머지는 eval 후보로 남긴다.
    by_app: dict[str, list[Step]] = defaultdict(list)
    for s in s2_only_pool:
        by_app[s.app].append(s)
    forced: list[Step] = []
    for app in sorted(by_app):
        items = by_app[app][:]
        rng.shuffle(items)
        k = min(len(items), max(1, round(len(items) * args.s2_only_train_frac)))
        forced.extend(items[:k])
    print(f"[s2-only] {len(by_app)} 앱 풀 {len(s2_only_pool)} → train {len(forced)} / "
          f"eval 잔여 {len(s2_only_pool) - len(forced)}")

    budget = args.n_s2 - len(forced)
    both_sel, both_meta = _take(both_pool, budget, rng, label="stage2_train(APP_BOTH)")
    if len(both_sel) != budget:
        _abort(f"stage2_train: APP_BOTH 표본 {len(both_sel)} != {budget}")
    train_sel = forced + both_sel
    rng.shuffle(train_sel)
    train_keys = {s.key for s in train_sel}
    train_eps = {s.ep for s in train_sel}
    print(f"[train] stage2_train {len(train_sel)} step / {len(train_eps)} 에피소드 / "
          f"{len({s.app for s in train_sel})} 앱")

    # ── 6. eval 버킷 7 종 ────────────────────────────────────────────────────
    # 후보 정의의 공통 축: "action 지도를 받지 않은 step". stage1 이 **state 로 본**
    # step 은 허용한다 (68 앱은 그러지 않으면 재고가 남지 않는다) — 대신 각 레코드에
    # s1_state_seen 플래그를 붙여 사후 분석이 가능하게 한다.
    supervised = s1_action | train_keys  # 합집합 1 회
    cand: dict[str, list[Step]] = {
        b: [s for s in usable if s.app in aset[b.upper()] and s.key not in supervised]
        for b in APP_BUCKETS
    }
    cand["step_id_s1"] = [s for s in usable
                          if s.key in s1_state and s.key not in supervised]
    cand["step_id_s2"] = [s for s in usable
                          if s.ep in train_eps and s.key not in supervised
                          and s.key not in s1_all]
    cand["step_ood"] = [s for s in usable
                        if s.ep not in s1_eps and s.ep not in train_eps
                        and s.key not in supervised]

    taken: set[str] = set()
    buckets: dict[str, list[Step]] = {}
    bucket_meta: dict[str, dict] = {}
    for name in BUCKET_ORDER:
        pool = [s for s in cand[name] if s.key not in taken]
        sel, meta = _take(pool, args.n_bucket, rng, label=name)
        meta["cand_before_dedup"] = len(cand[name])
        buckets[name] = sel
        bucket_meta[name] = meta
        taken |= {s.key for s in sel}

    # ── 7. 출력 ──────────────────────────────────────────────────────────────
    label = make_labeler(app_bucket_of, s1_state, s1_action, s1_eps, train_keys, train_eps)
    bak = work / "stage2_train.15k.bak.jsonl"
    cur = out_dir / "stage2_train.jsonl"
    n_cur = sum(1 for _ in cur.open()) if cur.exists() else 0
    if bak.exists():
        n_bak = sum(1 for _ in bak.open())
        if n_bak != 15000:
            _abort(f"백업 {bak.name} 이 {n_bak} 행 (15,000 기대) — 덮어쓰지 않는다")
        print(f"[backup] 기존 백업 유지 ({n_bak} 행)")
    elif n_cur == 15000:
        shutil.copy2(cur, bak)
        print(f"[backup] {cur.name}({n_cur} 행) → {bak}")
    else:
        _abort(f"{cur.name} 이 {n_cur} 행이고 백업도 없다 — 원본 15K 를 복구할 수 없다")

    _write_jsonl(cur, [label(records[s.idx], s, "stage2_train") for s in train_sel])
    for name in BUCKET_ORDER:
        _write_jsonl(out_dir / f"stage2_test_{name}.jsonl",
                     [label(records[s.idx], s, name) for s in buckets[name]])

    # ── 8. stage1 불변 재확인 + sidecar ──────────────────────────────────────
    post = snapshot_stage1(out_dir)
    fails = compare_stage1(baseline, post, label="post-build")
    if fails:
        for f in fails:
            print(f"[FAIL] {f}")
        _abort("빌드가 stage1 산출물을 건드렸다")
    print("[stage1] post-build sha256 불변 확인")

    pool_marginal = Counter(s.act for s in usable if s.key not in s1_all)
    src_by_group = Counter(app_bucket_of[s.app] for s in steps if s.app)
    pool_by_group = Counter(app_bucket_of[s.app] for s in usable if s.key not in s1_all)
    res = {
        "builder": "scripts/build_exp08_stage2_v2.py",
        "source": {"downstream": str(src_dir / SRC_DOWN), "meta": str(src_dir / SRC_META)},
        "seed": args.seed,
        "model": args.model,
        "revision_arg": args.revision,
        "targets": {"stage2_train": args.n_s2, "eval_bucket": args.n_bucket},
        "knobs": {
            "n_app_s1_only": args.n_app_s1_only,
            "n_app_s2_only": args.n_app_s2_only,
            "n_ood_episodes": args.n_ood_episodes,
            "s2_only_train_frac": args.s2_only_train_frac,
            "pool_headroom": POOL_HEADROOM,
            "skip_length_filter": bool(args.skip_length_filter),
        },
        "stage1_immutable": baseline,
        "exclusions": {
            "no_primary_app_steps": n_no_app,
            "legacy_test_steps": len(legacy),
            "legacy_test_files": list(LEGACY_TESTS),
            "length_filter_dropped": len(len_dropped),
            "reserved_ood_episodes": len(reserved_eps),
            "reserved_ood_steps": n_reserved_steps,
        },
        "app_partition": {
            g: {
                "n_apps": len(lst),
                "src_steps": src_by_group[g],       # 소스 86,431 기준
                "pool_steps": pool_by_group[g],     # usable ∖ stage1 step 기준
            }
            for g, lst in apps.items()
        },
        "app_lists": apps,
        "stage2_train": {
            "realized_n": len(train_sel),
            "n_apps": len({s.app for s in train_sel}),
            "n_episodes": len(train_eps),
            "forced_app_s2_only": len(forced),
            "app_both_sample": both_meta,
            "action_dist": dict(sorted(Counter(s.act for s in train_sel).items())),
            "pool_action_dist": dict(sorted(pool_marginal.items())),
        },
        "buckets": {
            name: {
                **bucket_meta[name],
                "n_apps": len({s.app for s in buckets[name]}),
                "n_episodes": len({s.ep for s in buckets[name]}),
                "action_dist": dict(sorted(Counter(s.act for s in buckets[name]).items())),
                "app_bucket_dist": dict(sorted(Counter(
                    app_bucket_of[s.app] for s in buckets[name]).items())),
                "s1_state_seen": sum(1 for s in buckets[name] if s.key in s1_state),
            }
            for name in BUCKET_ORDER
        },
        "files": {
            "stage2_train.jsonl": len(train_sel),
            **{f"stage2_test_{n}.jsonl": len(buckets[n]) for n in BUCKET_ORDER},
        },
    }
    (out_dir / "stage2_train.jsonl.meta.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2))
    print(f"[write] {out_dir / 'stage2_train.jsonl.meta.json'}")
    return res


# ── 검증 ─────────────────────────────────────────────────────────────────────


def verify(out_dir: Path, res: dict, src_dir: Path) -> int:  # noqa: PLR0915
    """산출물 불변식 검사. 0 = OK. 라벨을 믿지 않고 소스에서 다시 계산한다."""
    fails: list[str] = []

    # 1. stage1 산출물 바이트 불변
    fails += compare_stage1(res["stage1_immutable"], snapshot_stage1(out_dir), label="stage1")
    print(f"[verify] stage1 불가침 {len(STAGE1_IMMUTABLE)} 파일 sha256/size/mtime 대조: "
          f"{'동일' if not fails else '차이 발견'}")

    # 2. 행수
    loaded: dict[str, list[dict]] = {}
    for name, want in res["files"].items():
        p = out_dir / name
        if not p.exists():
            fails.append(f"{name} 없음")
            continue
        loaded[name] = _read_jsonl(p)
        if len(loaded[name]) != want:
            fails.append(f"{name}: {len(loaded[name])} != {want}")

    # 3. images prefix (하드 제약 12)
    for name, recs in loaded.items():
        bad = sum(1 for r in recs
                  if any(not i.startswith("AndroidControl/") for i in r.get("images", [])))
        if bad:
            fails.append(f"{name}: AndroidControl/ prefix 아닌 images {bad} 행")
    print(f"[verify] images prefix: {sum(len(v) for v in loaded.values())} 행 전수 검사")

    keys = {name: [_step_key(r) for r in recs] for name, recs in loaded.items()}
    for name, ks in keys.items():
        if len(set(ks)) != len(ks):
            fails.append(f"{name}: 파일 내부 중복 step {len(ks) - len(set(ks))}")

    # 4. stage2_train ∩ stage1_train (step 단위) = 0
    tr = set(keys.get("stage2_train.jsonl", []))
    s1_state, s1_action = load_stage1_step_keys(out_dir / "stage1_train.jsonl")
    s1_all = s1_state | s1_action
    n = len(tr & s1_all)
    print(f"[verify] stage2_train ∩ stage1_train(state∪action) = {n}")
    if n:
        fails.append(f"stage2_train ∩ stage1_train = {n} (0 이어야 함)")

    # 5. stage2_train ∩ 각 버킷 = 0, 버킷 상호 교집합 = 0
    names = [f"stage2_test_{b}.jsonl" for b in BUCKET_ORDER]
    for name in names:
        n = len(tr & set(keys.get(name, [])))
        if n:
            fails.append(f"stage2_train ∩ {name} = {n}")
    pair_max = 0
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            n = len(set(keys.get(a, [])) & set(keys.get(b, [])))
            pair_max = max(pair_max, n)
            if n:
                fails.append(f"{a} ∩ {b} = {n}")
    print(f"[verify] stage2_train ∩ 버킷 7 = 0 / 버킷 쌍 21 조합 최대 교집합 {pair_max}")

    # 6. 기존 평가셋과의 교집합 = 0 (train·신규 버킷 양쪽)
    legacy = set()
    for ln in LEGACY_TESTS:
        legacy |= load_step_keys(out_dir / ln)
    leaks = {name: len(set(ks) & legacy) for name, ks in keys.items()}
    if any(leaks.values()):
        fails.append(f"기존 평가셋 누출: {dict((k, v) for k, v in leaks.items() if v)}")
    print(f"[verify] 기존 평가셋({'+'.join(LEGACY_TESTS)}, {len(legacy)} step) 교집합: "
          f"{sum(leaks.values())}")

    # 7. APP_S1_ONLY·APP_OOD 앱이 stage2_train 에 0 건 (라벨이 아니라 meta 에서 재계산)
    ep2app = {}
    with (src_dir / SRC_META).open() as f:
        for line in f:
            r = json.loads(line)
            ep2app[int(r["episode_id"])] = r.get("primary_app")
    forbidden = set(res["app_lists"]["APP_S1_ONLY"]) | set(res["app_lists"]["APP_OOD"])
    app_bucket_of = {a: g for g, lst in res["app_lists"].items() for a in lst}
    bad = [r for r in loaded.get("stage2_train.jsonl", [])
           if ep2app.get(int(_EP_RE.search(_step_key(r)).group(1))) in forbidden]
    print(f"[verify] stage2_train 안의 APP_S1_ONLY∪APP_OOD 앱 step = {len(bad)}")
    if bad:
        fails.append(f"stage2_train 에 홀드아웃 앱 step {len(bad)} 건")

    # 8. 라벨 정합성 — primary_app / app_bucket 이 소스와 일치하는가
    mismatch = 0
    for recs in loaded.values():
        for r in recs:
            app = ep2app.get(int(_EP_RE.search(_step_key(r)).group(1)))
            if r.get("primary_app") != app or r.get("app_bucket") != app_bucket_of.get(app):
                mismatch += 1
    print(f"[verify] primary_app/app_bucket 라벨 재계산 불일치 {mismatch}")
    if mismatch:
        fails.append(f"라벨 불일치 {mismatch} 행")
    for b in STEP_BUCKETS:
        recs = loaded.get(f"stage2_test_{b}.jsonl", [])
        wrong = sum(1 for r in recs if r.get("step_bucket") != b)
        if wrong:
            fails.append(f"stage2_test_{b}: step_bucket != {b} 인 행 {wrong}")
    for b in APP_BUCKETS:
        recs = loaded.get(f"stage2_test_{b}.jsonl", [])
        want = {"app_both": "APP_BOTH", "app_s1_only": "APP_S1_ONLY",
                "app_s2_only": "APP_S2_ONLY", "app_ood": "APP_OOD"}[b]
        wrong = sum(1 for r in recs if r.get("app_bucket") != want)
        if wrong:
            fails.append(f"stage2_test_{b}: app_bucket != {want} 인 행 {wrong}")

    # 9. 실현 표
    print("\n[verify] 앱 파티션")
    print(f"{'group':<12}{'apps':>6}{'src_steps':>11}{'pool_steps':>12}")
    for g, d in res["app_partition"].items():
        print(f"{g:<12}{d['n_apps']:>6}{d['src_steps']:>11}{d['pool_steps']:>12}")

    print("\n[verify] 버킷 실현")
    print(f"{'bucket':<14}{'rows':>6}{'apps':>6}{'eps':>6}{'s1_state_seen':>15}  action_dist")
    tr_meta = res["stage2_train"]
    print(f"{'stage2_train':<14}{tr_meta['realized_n']:>6}{tr_meta['n_apps']:>6}"
          f"{tr_meta['n_episodes']:>6}{'-':>15}  {tr_meta['action_dist']}")
    for b in BUCKET_ORDER:
        d = res["buckets"][b]
        print(f"{b:<14}{d['realized_n']:>6}{d['n_apps']:>6}{d['n_episodes']:>6}"
              f"{d['s1_state_seen']:>15}  {d['action_dist']}")

    for f in fails:
        print(f"[FAIL] {f}")
    print("[verify] " + ("OK" if not fails else f"{len(fails)}건 실패"))
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source-dir", type=Path, default=PROJ / "data" / SRC_DEFAULT_SUBDIR)
    p.add_argument("--data-root", type=Path, default=PROJ / "data")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--revision", default=DEFAULT_REVISION,
                   help="processor commit SHA (길이 필터용). 정본 빌더와 같은 값을 쓴다")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--n-s2", type=int, default=N_S2_TRAIN)
    p.add_argument("--n-bucket", type=int, default=N_BUCKET,
                   help="eval 버킷 목표 N. 재고가 모자라면 상한까지만 뽑는다")
    p.add_argument("--n-app-s1-only", type=int, default=N_APP_S1_ONLY,
                   help="stage2_train 에서 홀드아웃할 P_ACTION 앱 수")
    p.add_argument("--n-app-s2-only", type=int, default=N_APP_S2_ONLY,
                   help="P_STATE_ONLY 68 앱 중 stage2 가 보게 할 앱 수 (나머지는 APP_OOD)")
    p.add_argument("--n-ood-episodes", type=int, default=N_OOD_EPISODES,
                   help="step_ood 재고 확보용으로 train 풀에서 뺄 에피소드 수")
    p.add_argument("--s2-only-train-frac", type=float, default=S2_ONLY_TRAIN_FRAC,
                   help="APP_S2_ONLY 앱의 풀 step 중 train 에 넣을 비율")
    p.add_argument("--skip-length-filter", action="store_true",
                   help="이미지가 없는 환경에서 임시 우회 (프로덕션 빌드에서는 쓰지 마라)")
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args(argv)

    out_dir = args.data_root / OUT_SUBDIR
    if args.verify_only:
        return verify(out_dir, json.loads(
            (out_dir / "stage2_train.jsonl.meta.json").read_text()), args.source_dir)
    return verify(out_dir, build(args), args.source_dir)


if __name__ == "__main__":
    raise SystemExit(main())
