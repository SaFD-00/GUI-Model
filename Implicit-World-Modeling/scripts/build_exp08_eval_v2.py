#!/usr/bin/env python3
"""AC_EXP08 eval 데이터 전면 교체 (v2, 2026-09-08) — train 은 손대지 않는다.

무엇이 왜 바뀌는가
------------------
구 eval 은 **stage 축**으로 갈려 있었다 (stage1 test 4 종 · stage2 test 7 버킷).
새 eval 은 **task 축**으로 가른다 — `state-prediction` 과 `action-prediction` 이고,
각 task 안에서 **trajectory(에피소드) 단위**로 ID / OOD 를 나눈다. 사용자 결정
(2026-09-08 인터뷰):

  state-prediction   : ID / OOD (stage1 기준) × {full, masked, dropped}  = 6 파일
  action-prediction  : stage1 ID/OOD + stage2 ID/OOD + 공통 OOD          = 5 파일

train 4 파일 (메인 stage1·stage2 + stage1 ablation 2 종) 은 **동결**이다. 이 빌더는
읽기만 하고, 그 step 을 후보 풀에서 전량 제외한다 (`TRAIN_FILES` 주석 참조 — ablation 을
빠뜨리면 inverse-mix 계보에서 OOD 라벨이 거짓이 된다).

train 동결이 만든 재고 상한 (실측, 2026-09-08 · ablation 제외 반영)
------------------------------------------------------------------
후보 = 소스 step 중 어느 train 에도 안 쓰인 것. 셀은 `(ep ∈ stage1 계보, ep ∈ stage2_train)`.

    셀        action 소스   state 소스
    (T,T)        5,504        2,457
    (T,F)        3,415        1,307
    (F,T)        1,343          969
    (F,F)        1,852        1,176   ← 엄격 OOD (어느 train 에도 없는 trajectory)

여기서 **앱 축 OOD 는 성립하지 않는다** — train 에 한 번도 안 나온 앱은 1 개(4 step)뿐이다.
그래서 ID/OOD 축은 앱이 아니라 **trajectory** 다. "1000 개" 도 trajectory 가 아니라
step 단위다 (완전 미학습 trajectory 는 379 개뿐이라 trajectory 1000 은 불가능하다).

셀 배정 (5 + 2 버킷 전부 disjoint)
----------------------------------
    action_test_s1_id   ← (T,T)      action_test_s1_ood  ← (F,T)
    action_test_s2_id   ← (T,T)      action_test_s2_ood  ← (T,F)
                                     action_test_ood     ← (F,F)
    state_test_id       ← (T,T)+(T,F)
    state_test_ood      ← (F,F) 우선, 부족분만 (F,T)

⚠ `state_test_ood` 의 (F,T) 혼입분은 **stage2 계보 모델에겐 OOD 가 아니다** (stage2_train
이 본 에피소드다). 레코드의 `s2_ep_seen` 으로 사후 배제할 수 있고 sidecar 가 건수를 남긴다.
같은 이유로 `action_test_s1_ood` 는 stage1 계보 전용, `action_test_s2_ood` 는 stage2 계보
전용이다 — 두 계보를 한 기준선으로 비교하려면 `action_test_ood` (엄격) 를 쓴다.

왜 5 파일이 같은 action mix 를 갖는가
-------------------------------------
구 stage2 버킷은 버킷마다 action mix 가 달라 `step_accuracy` 원값 비교가 mix 효과에
오염됐고, "macro 로 읽어라" 는 사후 보정으로 살았다 (ARCHITECTURE §6). 이번엔 **분포를
맞춰 굽는다** — 5 파일의 실현 mix 가 완전히 같아야 하고 `verify()` 가 그것을 강제한다.

쿼터는 2-pass 다. **한 셀이 k 개 버킷을 공급하면 그 셀의 가용량을 k 로 나눠야 한다**
((T,T) 는 s1_id·s2_id 둘을 먹인다). 이걸 빠뜨리면 pass 1 은 통과하고 두 번째 버킷이
추출 단계에서 조용히 미달한다.

`long_press` · `navigate_home` 은 목표 0 이다 — 최소 셀 재고가 각각 1 · 0 건이라 통계가
아니다. 재고 상한을 완화하지 않고 제외 사유를 sidecar 에 남기는 것이 이 저장소의 선례다.
같은 이유로 `navigate_back` 은 1.4% 로 얇다 — inverse-mix 가 그 층을 많이 학습해 ID 셀의
가용량이 20/버킷까지 내려갔다. 얇더라도 **5 파일이 같아야** 한다는 제약이 우선이다.

Usage
-----
  python scripts/build_exp08_eval_v2.py --dry-run     # 쿼터·실현 mix 만 출력 (파일 안 굽는다)
  python scripts/build_exp08_eval_v2.py
  python scripts/build_exp08_eval_v2.py --verify-only

conda env ``implicit-world-modeling`` 의 python 으로 실행한다 (transformers 필요).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
SCRIPTS = PROJ / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_exp08_data import (  # noqa: E402
    COPY_FILTER_THR,
    CUTOFF_LEN,
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    DEFAULT_SEED,
    IMG_MAX_PIXELS,
    IMG_MIN_PIXELS,
    _abort,
    _action_of_gpt,
    _episode_of,
    _episode_roundrobin,
    _given_action_of_state,
    _read_jsonl,
    _write_jsonl,
    attach_raw_current_state,
    filter_pool_by_copy,
    filter_pool_by_length,
    make_three_formats,
    remap_image,
    remap_record,
)

# ── 상수 ─────────────────────────────────────────────────────────────────────
SRC_STATE = "EXP08_stage1_state.jsonl"
SRC_DOWN = "EXP08_stage2.jsonl"
SRC_META = "episodes_meta.jsonl"
SRC_DEFAULT_SUBDIR = "AndroidControl"
OUT_SUBDIR = "AndroidControl_EXP08"

# 동결된 train — 읽기만 한다. 이 step 은 후보 풀에서 전량 빠진다.
#
# ⚠ **ablation 계보를 빠뜨리면 안 된다.** `stage1_train_inverse_mix.jsonl` 은 부모
# `stage1_train.jsonl` 의 부분집합이 **아니다** — inverse 표본을 In-domain 앱에서 새로
# 뽑으므로 부모 밖 step 3,162 개(에피소드 71 개)를 학습한다 (실측 2026-09-08). 그것을
# 제외하지 않으면 inverse-mix 계보에서 `action_test_s1_ood` 3 행 · `state_test_ood` 6 행이
# **학습한 step 을 OOD 라고 부르게 된다.** `stage1_train_action_only.jsonl` 은 부모의 라인
# 단위 필터라 부분집합이 맞지만(실측 차집합 0), 규약이 아니라 실측으로 확인한 것이므로
# 목록에 함께 둔다 — 새 ablation 이 생기면 **여기에 추가하는 것이 첫 번째 할 일**이다.
TRAIN_FILES = (
    "stage1_train.jsonl",
    "stage1_train_action_only.jsonl",
    "stage1_train_inverse_mix.jsonl",
    "stage2_train.jsonl",
)
STAGE1_TRAIN_FILES = TRAIN_FILES[:3]
STAGE2_TRAIN_FILES = TRAIN_FILES[3:]

N_BUCKET = 1000

# action mix (5 파일 공통, per 1000). 상한은 **버킷별 가용량의 최소**이고, ID 셀 (T,T) 는
# 두 버킷(s1_id·s2_id)을 먹이므로 그 셀만 절반으로 친다. ablation 계보까지 제외한 실측:
#   click 783 / terminate 173 / swipe 167 / type 87 / open 73 / navigate_back 20
# open·navigate_back 이 좁은 이유는 inverse-mix 가 그 층을 많이 학습했기 때문이다
# ((T,T) 의 navigate_back 이 147 → 41 로 줄었다). 목표는 그 아래로 마진을 두고 잡는다 —
# 길이 필터가 몇 건 떨어뜨려도 5 파일이 **같은** 실현 mix 를 유지해야 하기 때문이다.
ACTION_MIX = {
    "click": 550,
    "terminate": 150,
    "swipe": 150,
    "type": 78,
    "open": 58,
    "navigate_back": 14,
}
# state mix (2 파일 공통, per 1000). state 소스에는 terminate 가 없다 (7 종).
# 캡은 OOD 풀 ((F,F)+(F,T)): click 1459 / swipe 319 / open 187 / type 104 / navigate_back 85.
# 자연 분포는 click 67% 인데, 화면이 크게 바뀌는 action(open·navigate_back)의 비중을 올려
# "복사로 맞힐 수 있는" 샘플 비중을 낮춘다.
STATE_MIX = {
    "click": 570,
    "swipe": 150,
    "open": 130,
    "type": 80,
    "navigate_back": 70,
}
# 재고 부족으로 목표에서 제외한 action (사유는 sidecar 에 남는다).
EXCLUDED_ACTIONS = ("long_press", "navigate_home")
# 쿼터가 재고에 막혀 미달하면 그 몫을 흡수하는 층.
ABSORBER = "click"

# 버킷 → 공급 셀 (순서 = 우선순위). 셀 키는 (s1_seen, s2_seen).
CELL_TT, CELL_TF, CELL_FT, CELL_FF = (True, True), (True, False), (False, True), (False, False)
ACTION_BUCKETS: dict[str, tuple] = {
    "s1_id": (CELL_TT,),
    "s2_id": (CELL_TT,),
    "s1_ood": (CELL_FT,),
    "s2_ood": (CELL_TF,),
    "ood": (CELL_FF,),
}
STATE_BUCKETS: dict[str, tuple] = {
    "id": (CELL_TT, CELL_TF),
    "ood": (CELL_FF, CELL_FT),  # 엄격 셀 우선 — (F,T) 는 부족분만
}
STATE_FORMATS = ("full", "masked", "dropped")

ACTION_FILES = tuple(f"action_test_{b}.jsonl" for b in ACTION_BUCKETS)
STATE_FILES = tuple(
    f"state_test_{b}_{f}.jsonl" for b in STATE_BUCKETS for f in STATE_FORMATS
)
SIDECAR = "eval_v2.meta.json"


# ── 후보 항목 ────────────────────────────────────────────────────────────────


class Cand:
    """후보 한 건 — 레코드 + 층화/라벨에 필요한 축."""

    __slots__ = ("rec", "action", "episode", "step", "key", "cell")

    def __init__(self, rec: dict, action: str, episode: int, step: int, key: str, cell: tuple):
        self.rec = rec
        self.action = action
        self.episode = episode
        self.step = step
        self.key = key
        self.cell = cell


def _step_key(rec: dict) -> str:
    """레코드 → 공유 이미지 풀 기준의 정규 step 키 (`AndroidControl/images/...`)."""
    img = rec["images"][0]
    return img if img.startswith("AndroidControl/") else remap_image(img)


def _step_no(key: str) -> int:
    return int(key.rsplit("_step_", 1)[1].split(".", 1)[0])


def load_train_episodes(out_dir: Path) -> dict:
    """동결 train 4 파일에서 step 키 집합과 에피소드 집합을 만든다.

    `s1_eps` 는 **stage1 계보 전체**(메인 + ablation)가 본 에피소드다. OOD 판정의 기준이
    이것이어야 어느 stage1 체크포인트로 채점해도 라벨이 참이다. 메인 train 안의 state/action
    구분은 별도로 남긴다 (`fmt` 키 유무 규약 — 부모 파일 안에서만 유효하다, 하드 제약 15h(a)).
    """
    def eps(keys: set[str]) -> set[int]:
        return {int(k.split("episode_")[1].split("_step_")[0]) for k in keys}

    s1_state_steps: set[str] = set()
    s1_action_steps: set[str] = set()
    with (out_dir / "stage1_train.jsonl").open() as f:
        for line in f:
            rec = json.loads(line)
            (s1_state_steps if "fmt" in rec else s1_action_steps).add(_step_key(rec))
    s1_steps = s1_state_steps | s1_action_steps
    s1_all_steps = set(s1_steps)
    variant_extra: dict[str, int] = {}
    for name in STAGE1_TRAIN_FILES[1:]:
        p = out_dir / name
        if not p.exists():
            _abort(f"stage1 ablation train 이 없다: {p} (TRAIN_FILES 참조)")
        with p.open() as f:
            ks = {_step_key(json.loads(line)) for line in f}
        variant_extra[name] = len(ks - s1_steps)
        s1_all_steps |= ks
    s2_steps: set[str] = set()
    for name in STAGE2_TRAIN_FILES:
        with (out_dir / name).open() as f:
            s2_steps |= {_step_key(json.loads(line)) for line in f}

    info = {
        "s1_steps": s1_all_steps,
        "s2_steps": s2_steps,
        "train_steps": s1_all_steps | s2_steps,
        "s1_eps": eps(s1_all_steps),          # OOD 판정 기준 — 계보 전체
        "s1_state_eps": eps(s1_state_steps),  # 메인 train 내역 (사후 층화용)
        "s1_action_eps": eps(s1_action_steps),
        "s2_eps": eps(s2_steps),
        "variant_extra_steps": variant_extra,
    }
    print(
        f"[train] stage1 메인 step {len(s1_steps)} (state {len(s1_state_steps)} / action {len(s1_action_steps)}) "
        f"+ ablation 추가분 {variant_extra} → stage1 계보 전체 {len(s1_all_steps)}"
    )
    print(f"[train] stage2 step {len(s2_steps)} → 제외 대상 {len(info['train_steps'])} step")
    print(f"[train] 에피소드 stage1 계보 {len(info['s1_eps'])} / stage2 {len(info['s2_eps'])}")
    return info


def load_candidates(src_dir: Path, train: dict, *, limit: int | None) -> tuple[list[Cand], list[Cand]]:
    """두 소스를 읽어 train step 을 제외한 후보 리스트를 만든다 (state, action)."""

    def build(fname: str, action_of, label: str) -> list[Cand]:
        raw = _read_jsonl(src_dir / fname)
        if limit:
            raw = raw[:limit]
        out: list[Cand] = []
        for i, rec in enumerate(raw):
            key = _step_key(rec)
            if key in train["train_steps"]:
                continue
            ep = int(_episode_of(rec, fname, i))
            cell = (ep in train["s1_eps"], ep in train["s2_eps"])
            out.append(Cand(rec, action_of(rec, fname, i), ep, _step_no(key), key, cell))
        print(f"[load] {label}: 소스 {len(raw)} → 미학습 후보 {len(out)}")
        return out

    state = build(SRC_STATE, _given_action_of_state, "state")
    action = build(SRC_DOWN, _action_of_gpt, "action")
    return state, action


# ── 쿼터 (2-pass, 셀 단위) ───────────────────────────────────────────────────


def _by_cell_action(cands: list[Cand]) -> dict[tuple, dict[str, list[Cand]]]:
    out: dict[tuple, dict[str, list[Cand]]] = defaultdict(lambda: defaultdict(list))
    for c in cands:
        out[c.cell][c.action].append(c)
    return out


def compute_quota(
    cands: list[Cand], buckets: dict[str, tuple], target: dict[str, int], n: int, *, label: str
) -> tuple[dict[str, int], dict]:
    """5(2) 버킷이 **공유할 수 있는** action 쿼터를 계산한다.

    pass 1 — 버킷별 가용량. 한 셀이 k 개 버킷을 공급하면 그 셀 가용량을 k 로 나눈다.
             ((T,T) 가 s1_id·s2_id 를 동시에 먹이므로 이 나눗셈이 없으면 두 번째
             버킷이 추출 단계에서 조용히 미달한다.)
    pass 2 — action 별 **전 버킷 공통 최소**로 쿼터를 하향하고, 잔여를 ABSORBER 로 흡수.
    """
    pools = _by_cell_action(cands)
    share = Counter(cell for cells in buckets.values() for cell in cells)
    avail: dict[str, dict[str, int]] = {}
    for b, cells in buckets.items():
        avail[b] = {
            a: sum(len(pools[c].get(a, [])) // share[c] for c in cells)
            for a in target
        }
    cap = {a: min(avail[b][a] for b in buckets) for a in target}
    quota = {a: min(target[a], cap[a]) for a in target}
    short = {a: target[a] - quota[a] for a in target if quota[a] < target[a]}
    deficit = n - sum(quota.values())
    if deficit > 0:
        room = cap[ABSORBER] - quota[ABSORBER]
        quota[ABSORBER] += min(deficit, room)
    total = sum(quota.values())
    if total != n:
        _abort(
            f"{label}: 재고 상한으로 {n} 을 채우지 못함 (실현 {total}). "
            f"cap={cap} quota={quota} — 목표 N 을 낮추거나 mix 를 재조정하라."
        )
    print(f"[quota] {label}: cap={cap}")
    print(f"[quota] {label}: quota={quota}" + (f"  (목표 미달: {short})" if short else ""))
    return quota, {
        "target_mix": dict(target),
        "cap": cap,
        "quota": quota,
        "target_shortfall": short,
        "bucket_availability": avail,
        "cell_share": {f"{k[0]:d}{k[1]:d}": v for k, v in share.items()},
        "excluded_actions": {
            a: "최소 셀 재고 ≤1 건 — 통계가 아니라 제외" for a in EXCLUDED_ACTIONS
        },
    }


def draw(
    cands: list[Cand],
    buckets: dict[str, tuple],
    quota: dict[str, int],
    rng: random.Random,
    *,
    label: str,
) -> dict[str, list[Cand]]:
    """버킷별로 쿼터만큼 뽑는다. 셀 우선순위 순으로 채우고, 뽑힌 항목은 즉시 회수한다."""
    pools = _by_cell_action(cands)
    taken: set[str] = set()
    out: dict[str, list[Cand]] = {}
    for b, cells in buckets.items():
        picked: list[Cand] = []
        for a in sorted(quota):
            need = quota[a]
            for cell in cells:  # 우선순위 순 (state OOD 는 엄격 셀부터)
                if need <= 0:
                    break
                avail = [c for c in pools[cell].get(a, []) if c.key not in taken]
                if not avail:
                    continue
                ordered = _episode_roundrobin(avail, rng, ep_of=lambda c: c.episode)
                chunk = ordered[:need]
                for c in chunk:
                    picked.append(c)
                    taken.add(c.key)
                need -= len(chunk)
            if need > 0:
                _abort(f"{label}/{b}: action={a} 쿼터 {quota[a]} 중 {need} 건 미충족")
        rng.shuffle(picked)
        out[b] = picked
        mix = Counter(c.action for c in picked)
        cells_used = Counter(f"{c.cell[0]:d}{c.cell[1]:d}" for c in picked)
        print(f"[draw] {label}/{b}: {len(picked)} 건  mix={dict(sorted(mix.items()))}  cells={dict(sorted(cells_used.items()))}")
    return out


# ── 라벨 ─────────────────────────────────────────────────────────────────────


def label_of(c: Cand, bucket: str, train: dict, ep2app: dict[int, str]) -> dict:
    """레코드에 실을 사후 층화용 라벨. 라벨을 믿지 않고 verify 가 재계산한다."""
    return {
        "bucket": bucket,
        "episode_id": c.episode,
        "step_id": c.step,
        "primary_app": ep2app.get(c.episode),
        "action_type": c.action,
        "s1_ep_seen": c.episode in train["s1_eps"],
        "s1_state_ep_seen": c.episode in train["s1_state_eps"],
        "s1_action_ep_seen": c.episode in train["s1_action_eps"],
        "s2_ep_seen": c.episode in train["s2_eps"],
    }


# ── 빌드 ─────────────────────────────────────────────────────────────────────


def build(args: argparse.Namespace) -> dict:  # noqa: PLR0915  (선형 파이프라인)
    src_dir = args.source_dir
    out_dir = args.data_root / OUT_SUBDIR
    work = out_dir / "_build_eval_v2"
    rng = random.Random(args.seed)

    for f in TRAIN_FILES:
        if not (out_dir / f).exists():
            _abort(f"동결 train 이 없다: {out_dir / f} (ablation 계보 포함 — TRAIN_FILES)")

    ep2app: dict[int, str] = {}
    with (src_dir / SRC_META).open() as f:
        for line in f:
            r = json.loads(line)
            ep2app[int(r["episode_id"])] = r.get("primary_app")

    # ── 1. 동결 train → 제외 집합 + 셀 축 ────────────────────────────────────
    train = load_train_episodes(out_dir)

    # ── 2. 후보 로드 (train step 전량 제외) ──────────────────────────────────
    state_c, action_c = load_candidates(src_dir, train, limit=args.limit)

    # ── 3. 필터 ──────────────────────────────────────────────────────────────
    # 길이 필터: 구 stage1 test 는 무손실이었지만 stage2 v2 버킷부터는 eval 에도 건다.
    # 추론 입력이 cutoff 를 넘으면 잘린 프롬프트로 채점하게 된다.
    len_drop = {"state": 0, "action": 0, "skipped": bool(args.skip_length_filter)}
    if not args.skip_length_filter:
        from filter_long_samples import build_length_fn  # noqa: PLC0415
        from transformers import AutoProcessor  # noqa: PLC0415

        proc = AutoProcessor.from_pretrained(
            args.model, revision=args.revision, trust_remote_code=True
        )
        length_of = build_length_fn(
            proc, image_max_pixels=IMG_MAX_PIXELS, image_min_pixels=IMG_MIN_PIXELS
        )
        media_dir = out_dir.parent

        def _len_filter(cands: list[Cand], label: str) -> tuple[list[Cand], int]:
            kept, dropped = filter_pool_by_length(
                [(c.rec, c) for c in cands], length_of, media_dir, CUTOFF_LEN, label=label
            )
            return [t[1] for t in kept], dropped

        state_c, len_drop["state"] = _len_filter(state_c, "state")
        action_c, len_drop["action"] = _len_filter(action_c, "action")

    # copy 필터는 state 후보에만 — 이 실험의 지표가 복사율이라 "복사가 정답" 인 샘플이
    # 섞이면 copy_excess 류가 구조적으로 부풀어 anti-copy 효과가 가려진다.
    kept, copy_drop = filter_pool_by_copy(
        [(c.rec, c) for c in state_c], args.copy_thr, label="state-eval"
    )
    state_c = [t[1] for t in kept]

    # ── 4. 쿼터 (2-pass) ─────────────────────────────────────────────────────
    a_quota, a_qmeta = compute_quota(
        action_c, ACTION_BUCKETS, ACTION_MIX, args.n_bucket, label="action"
    )
    s_quota, s_qmeta = compute_quota(
        state_c, STATE_BUCKETS, STATE_MIX, args.n_bucket, label="state"
    )

    # ── 5. 추출 ──────────────────────────────────────────────────────────────
    a_sel = draw(action_c, ACTION_BUCKETS, a_quota, rng, label="action")
    s_sel = draw(state_c, STATE_BUCKETS, s_quota, rng, label="state")

    if args.dry_run:
        print("\n[dry-run] 파일을 굽지 않고 종료한다.")
        return {"dry_run": True, "action_quota": a_qmeta, "state_quota": s_qmeta}

    work.mkdir(parents=True, exist_ok=True)

    # ── 6. action 파일 5 종 ──────────────────────────────────────────────────
    action_files: dict[str, int] = {}
    for b, picked in a_sel.items():
        recs = [
            {**remap_record(dict(c.rec)), **label_of(c, f"action_{b}", train, ep2app)}
            for c in picked
        ]
        name = f"action_test_{b}.jsonl"
        _write_jsonl(out_dir / name, recs)
        action_files[name] = len(recs)

    # ── 7. state 파일 6 종 — 같은 1000 원본을 세 포맷으로 각각 굽는다 ────────
    # 포맷 간 난이도 교란을 없애려는 설계다: 세 파일의 sample_id 집합이 같아야 하고
    # raw_current_state 도 바이트 동일해야 한다 (verify 가 강제).
    fmt_ratio = {"full": (1.0, 0.0, 0.0), "masked": (0.0, 1.0, 0.0), "dropped": (0.0, 0.0, 1.0)}
    state_files: dict[str, int] = {}
    for b, picked in s_sel.items():
        base = [remap_record(dict(c.rec)) for c in picked]
        # build_wm_formats 는 알 수 없는 키를 버린다 → 변환 뒤 step 키로 조인한다.
        lab_by_key = {c.key: label_of(c, f"state_{b}", train, ep2app) for c in picked}
        for fmt, ratios in fmt_ratio.items():
            raw_p, applied_p = make_three_formats(
                base, work, f"state_test_{b}_{fmt}", seed=args.seed, ratios=ratios
            )
            recs = attach_raw_current_state(applied_p, raw_p)
            out = []
            for r in recs:
                k = _step_key(r)
                if k not in lab_by_key:
                    _abort(f"state_{b}/{fmt}: 라벨 조인 실패 (step={k})")
                out.append({**r, **lab_by_key[k]})
            name = f"state_test_{b}_{fmt}.jsonl"
            _write_jsonl(out_dir / name, out)
            state_files[name] = len(out)

    # ── 8. sidecar ───────────────────────────────────────────────────────────
    def _mix(name: str) -> dict:
        return dict(sorted(Counter(r["action_type"] for r in _read_jsonl(out_dir / name)).items()))

    def _cells(name: str) -> dict:
        recs = _read_jsonl(out_dir / name)
        c = Counter(f"s1{'T' if r['s1_ep_seen'] else 'F'}_s2{'T' if r['s2_ep_seen'] else 'F'}" for r in recs)
        return dict(sorted(c.items()))

    res = {
        "builder": "scripts/build_exp08_eval_v2.py",
        "built_at": args.stamp,
        "source": {
            "state": str(src_dir / SRC_STATE),
            "action": str(src_dir / SRC_DOWN),
            "meta": str(src_dir / SRC_META),
        },
        "frozen_train": list(TRAIN_FILES),
        "stage1_variant_extra_steps": train["variant_extra_steps"],
        "seed": args.seed,
        "model": args.model,
        "revision_arg": args.revision,
        "n_bucket": args.n_bucket,
        "design": {
            "axis": "task (state-prediction / action-prediction) × trajectory ID/OOD",
            "id_ood_unit": "episode(trajectory) 통째 — step 단위가 아니다",
            "action_buckets": {b: [f"{c[0]:d}{c[1]:d}" for c in cells] for b, cells in ACTION_BUCKETS.items()},
            "state_buckets": {b: [f"{c[0]:d}{c[1]:d}" for c in cells] for b, cells in STATE_BUCKETS.items()},
            "cell_key": "(ep ∈ stage1_train, ep ∈ stage2_train)",
            "app_axis_note": "train 동결 상태에서 train 미등장 앱은 1 개(4 step) — 앱 축 OOD 는 성립하지 않는다",
        },
        "filters": {
            "length": {
                "cutoff_len": CUTOFF_LEN,
                "image_max_pixels": IMG_MAX_PIXELS,
                "image_min_pixels": IMG_MIN_PIXELS,
                **len_drop,
            },
            "copy": {"version": "v2c", "threshold": args.copy_thr, "state_dropped": copy_drop,
                     "note": "state 후보에만 건다 (지표가 복사율이라)"},
        },
        "action_quota": a_qmeta,
        "state_quota": s_qmeta,
        "files": {**action_files, **state_files},
        "realized_mix": {n: _mix(n) for n in list(action_files) + list(state_files)},
        "exposure_cells": {n: _cells(n) for n in list(action_files) + list(state_files)},
    }
    (out_dir / SIDECAR).write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"[write] {out_dir / SIDECAR}")
    return res


# ── 검증 ─────────────────────────────────────────────────────────────────────


def verify(out_dir: Path, res: dict, src_dir: Path) -> int:  # noqa: PLR0915
    """산출물 불변식 검사. 0 = OK. 라벨을 믿지 않고 소스·train 에서 재계산한다."""
    fails: list[str] = []

    loaded: dict[str, list[dict]] = {}
    for name, want in res["files"].items():
        p = out_dir / name
        if not p.exists():
            fails.append(f"{name} 없음")
            continue
        loaded[name] = _read_jsonl(p)
        if len(loaded[name]) != want:
            fails.append(f"{name}: {len(loaded[name])} != {want}")
    print(f"[verify] 파일 {len(loaded)}/{len(res['files'])} 행수 대조")

    keys = {n: [_step_key(r) for r in recs] for n, recs in loaded.items()}
    for n, ks in keys.items():
        if len(set(ks)) != len(ks):
            fails.append(f"{n}: 파일 내부 중복 step {len(ks) - len(set(ks))}")

    # 1. images prefix (하드 제약 12)
    bad = {
        n: sum(1 for r in recs if any(not i.startswith("AndroidControl/") for i in r.get("images", [])))
        for n, recs in loaded.items()
    }
    if any(bad.values()):
        fails.append(f"images prefix 위반: {dict((k, v) for k, v in bad.items() if v)}")
    print(f"[verify] images prefix: {sum(len(v) for v in loaded.values())} 행 전수")

    # 2. train ∩ eval = 0 (동결 train 에서 재계산)
    train = load_train_episodes(out_dir)
    leaks = {n: len(set(ks) & train["train_steps"]) for n, ks in keys.items()}
    if any(leaks.values()):
        fails.append(f"train 누출: {dict((k, v) for k, v in leaks.items() if v)}")
    print(f"[verify] train(step {len(train['train_steps'])}) ∩ 전 eval 파일 = {sum(leaks.values())}")

    # 3. action 5 파일 상호 disjoint
    a_names = [n for n in loaded if n.startswith("action_test_")]
    pair_max = 0
    for i, a in enumerate(a_names):
        for b in a_names[i + 1:]:
            n = len(set(keys[a]) & set(keys[b]))
            pair_max = max(pair_max, n)
            if n:
                fails.append(f"{a} ∩ {b} = {n}")
    print(f"[verify] action 파일 쌍 {len(a_names) * (len(a_names) - 1) // 2} 조합 최대 교집합 {pair_max}")

    # 4. state ID ∩ OOD = 0 (포맷은 같은 원본이므로 full 끼리 비교)
    s_id = set(keys.get("state_test_id_full", []))
    s_ood = set(keys.get("state_test_ood_full", []))
    if s_id & s_ood:
        fails.append(f"state id ∩ ood = {len(s_id & s_ood)}")
    print(f"[verify] state id ∩ ood = {len(s_id & s_ood)}")

    # 5. state 3 포맷 = 같은 원본 (sample_id 집합 동일 · raw_current_state 바이트 동일)
    for b in STATE_BUCKETS:
        names = [f"state_test_{b}_{f}.jsonl" for f in STATE_FORMATS]
        if not all(n in loaded for n in names):
            continue
        sids = [{r["sample_id"] for r in loaded[n]} for n in names]
        if not (sids[0] == sids[1] == sids[2]):
            fails.append(f"state_{b}: 3 포맷 sample_id 집합 불일치")
        raws = [{r["sample_id"]: r["raw_current_state"] for r in loaded[n]} for n in names]
        diff = sum(1 for sid in sids[0] if not (raws[0][sid] == raws[1][sid] == raws[2][sid]))
        if diff:
            fails.append(f"state_{b}: raw_current_state 가 포맷 간 다른 샘플 {diff} 건")
        fmts = {n: {r["fmt"] for r in loaded[n]} for n in names}
        for n, f in fmts.items():
            want = n.rsplit("_", 1)[1].split(".")[0]
            if f != {want}:
                fails.append(f"{n}: fmt {f} != {{{want}}}")
        print(f"[verify] state_{b}: 3 포맷 sample_id 동일 · raw_current_state 동일 · fmt 단일")

    # 6. 실현 mix 가 그룹 안에서 완전히 동일 (이 설계의 핵심 — 버킷 간 mix 교란 제거)
    for group, names in (("action", a_names), ("state", [n for n in loaded if n.startswith("state_test_")])):
        mixes = {n: tuple(sorted(Counter(r["action_type"] for r in loaded[n]).items())) for n in names}
        uniq = set(mixes.values())
        if len(uniq) > 1:
            fails.append(f"{group}: 파일 간 action mix 불일치 {mixes}")
        print(f"[verify] {group} {len(names)} 파일 action mix 동일: {'예' if len(uniq) <= 1 else '아니오'}")

    # 7. 라벨 재계산 대조 (episodes_meta 와 동결 train 에서)
    ep2app: dict[int, str] = {}
    with (src_dir / SRC_META).open() as f:
        for line in f:
            r = json.loads(line)
            ep2app[int(r["episode_id"])] = r.get("primary_app")
    lab_bad = 0
    for n, recs in loaded.items():
        for r in recs:
            k = _step_key(r)
            ep = int(k.split("episode_")[1].split("_step_")[0])
            if (
                r["episode_id"] != ep
                or r["step_id"] != _step_no(k)
                or r["primary_app"] != ep2app.get(ep)
                or r["s1_ep_seen"] != (ep in train["s1_eps"])
                or r["s2_ep_seen"] != (ep in train["s2_eps"])
            ):
                lab_bad += 1
    if lab_bad:
        fails.append(f"라벨 재계산 불일치 {lab_bad} 행")
    print(f"[verify] 라벨 재계산 대조: 불일치 {lab_bad} 행")

    # 8. 셀 배정이 설계와 일치 (엄격 OOD 파일에 s1/s2 노출이 섞이면 안 된다)
    strict = "action_test_ood.jsonl"
    if strict in loaded:
        bad = sum(1 for r in loaded[strict] if r["s1_ep_seen"] or r["s2_ep_seen"])
        if bad:
            fails.append(f"{strict}: 엄격 OOD 인데 노출된 에피소드 {bad} 행")
        print(f"[verify] {strict} 엄격성(s1·s2 모두 미노출): 위반 {bad} 행")
    for n, want in (("action_test_s1_ood.jsonl", "s1_ep_seen"), ("action_test_s2_ood.jsonl", "s2_ep_seen")):
        if n in loaded:
            bad = sum(1 for r in loaded[n] if r[want])
            if bad:
                fails.append(f"{n}: {want}=true 인 행 {bad}")
    for n, want in (("action_test_s1_id.jsonl", "s1_ep_seen"), ("action_test_s2_id.jsonl", "s2_ep_seen"),
                    ("state_test_id_full.jsonl", "s1_ep_seen")):
        if n in loaded:
            bad = sum(1 for r in loaded[n] if not r[want])
            if bad:
                fails.append(f"{n}: {want}=false 인 행 {bad}")
    if "state_test_ood_full.jsonl" in loaded:
        bad = sum(1 for r in loaded["state_test_ood_full.jsonl"] if r["s1_ep_seen"])
        if bad:
            fails.append(f"state_test_ood_full.jsonl: s1_ep_seen=true 인 행 {bad}")
        mixed = sum(1 for r in loaded["state_test_ood_full.jsonl"] if r["s2_ep_seen"])
        print(f"[verify] state OOD 의 (F,T) 혼입(stage2 가 본 에피소드) = {mixed} 행 "
              f"— stage2 계보로 읽을 땐 s2_ep_seen=false 로 필터한다")

    if fails:
        print("\n[verify] 실패:")
        for m in fails:
            print("  - " + m)
        return 1
    print("\n[verify] 전 항목 통과")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", type=Path, default=PROJ / "data")
    ap.add_argument("--source-dir", type=Path, default=None,
                    help=f"기본: <data-root>/{SRC_DEFAULT_SUBDIR}")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--revision", default=DEFAULT_REVISION)
    ap.add_argument("--n-bucket", type=int, default=N_BUCKET)
    ap.add_argument("--copy-thr", type=float, default=COPY_FILTER_THR)
    ap.add_argument("--skip-length-filter", action="store_true",
                    help="길이 필터 생략 (빠른 리허설 전용 — 산출물을 커밋하지 마라)")
    ap.add_argument("--limit", type=int, default=None, help="소스 앞 N 행만 (스모크)")
    ap.add_argument("--stamp", default="2026-09-08", help="sidecar 에 남길 빌드 날짜")
    ap.add_argument("--dry-run", action="store_true", help="쿼터·실현 mix 만 출력하고 종료")
    ap.add_argument("--verify-only", action="store_true", help="기존 산출물만 검증")
    args = ap.parse_args(argv)
    if args.source_dir is None:
        args.source_dir = args.data_root / SRC_DEFAULT_SUBDIR

    out_dir = args.data_root / OUT_SUBDIR
    if args.verify_only:
        p = out_dir / SIDECAR
        if not p.exists():
            _abort(f"sidecar 없음: {p} (먼저 빌드하라)")
        return verify(out_dir, json.loads(p.read_text()), args.source_dir)

    res = build(args)
    if args.dry_run:
        return 0
    return verify(out_dir, res, args.source_dir)


if __name__ == "__main__":
    raise SystemExit(main())
