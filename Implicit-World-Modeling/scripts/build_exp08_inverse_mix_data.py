#!/usr/bin/env python3
"""AC_EXP08 stage1 inverse-mix 50K ablation 데이터 빌더 (forward 6 : inverse 2 : action 2).

메인 stage1(state 40K + action 10K, 총 50K)과 **총량이 같은** 대조군을 만든다. state
예측(forward)의 일부를 **역동역학(inverse dynamics)** 으로 갈아 끼웠을 때 downstream
action 성능이 어떻게 달라지는지 보려는 것이다.

  forward 30,000  ``stage1_train.jsonl`` 의 ``fmt`` 키를 **가진** 40K 에서 서브샘플.
                  3-포맷 비율 유지 (full 0.25 / masked 0.55 / dropped 0.2 →
                  7,500 / 16,500 / 6,000 = 각 포맷 재고의 정확히 75%).
  inverse 10,000  ``data/AndroidControl/EXP08_stage1_inverse.jsonl`` (67,554 행,
                  MID_ACTION_PREDICTION — current+next XML 을 주고 그 사이 action 을
                  맞히게 한다) 에서 선별. 조병웅 제공 0822 필터링본이고, 형제 원천
                  ``EXP08_stage1_state.jsonl`` / ``EXP08_stage2.jsonl`` 과 같은 자리에
                  같은 규칙(AGENTS 하드 제약 17 — 공유 폴더에 ``EXP{NN}_`` 접두, 벤더
                  파일명을 버린다) 으로 놓는다.
  action  10,000  ``stage1_train.jsonl`` 의 ``fmt`` 키가 **없는** 10K 전량, 기존 그대로.

산출 (``data/AndroidControl_EXP08/``):
  stage1_train_inverse_mix.jsonl            50,000 행
  stage1_train_inverse_mix.jsonl.meta.json  유래 sidecar

왜 ``task`` 키를 새로 붙이는가
------------------------------
기존 stage1 은 **"``fmt`` 키 부재 ⇒ action"** 이라는 암묵 규약으로 두 부분을 구분했다
(``build_exp08_ablation_data.py`` · ``build_exp08_stage2_v2.py::load_stage1_step_keys``
가 그 규약을 읽는다). inverse 가 같은 파일에 들어오는 순간 이 규약은 **에러 없이**
깨진다 — inverse 도 ``fmt`` 가 없어 action 으로 오분류된다. 그래서 모든 행에
``"task": "forward" | "inverse" | "action"`` 을 **명시적으로** 붙인다.

부모 원문 보존 (자체 검사)
--------------------------
forward·action 30K+10K 는 부모 ``stage1_train.jsonl`` 의 레코드에 ``task`` 하나만
더한 것이고, **``task`` 를 빼면 부모 라인과 바이트 동일**하다 (같은
``json.dumps(..., ensure_ascii=False)`` 설정 — ``_write_jsonl`` 재사용). 빌드와
``--verify-only`` 양쪽이 부모 라인 sha256 집합과 대조해 이를 강제한다.

inverse 는 부모가 벤더 파일이라 바이트 동일이 성립할 수 없다 — 이미지 remap(하드 제약
12) 과 ``token_weights`` 부착(stage1 YAML 이 ``use_diff_token_weighted_loss: true``)이
**필수**이기 때문이다. 대신 ``messages`` 원문 동일 + ``images`` == remap(벤더) +
추가 키가 정확히 ``{token_weights, task}`` 임을 빌드가 검사한다.

inverse 선별 규칙
-----------------
1. **eval 누출 금지** — EXP08 test 12 파일(stage1 4 + stage2 8) 에 등장하는 **에피소드**
   를 통째로 뺀다. step 단위로 빼면 같은 에피소드의 앞뒤 스텝이 남아 누출이 된다.
2. **In-domain 앱 한정** — ``stage2_train.jsonl.meta.json::app_lists`` 의
   ``APP_BOTH ∪ APP_S1_ONLY`` (754 앱). ``APP_OOD`` / ``APP_S2_ONLY`` 앱을 stage1
   train 에 넣으면 "OOD 앱은 stage1 train 에 한 번도 등장하지 않는다" 는 eval 파티션
   불변식이 깨진다. 파티션을 **재계산하지 않고** meta 를 읽는다 (AGENTS "App partition
   을 재계산하지 마라").
3. **길이 필터** — cutoff_len 24576 / max_pixels 1,605,632 / min_pixels 3,136
   (하드 제약 4 — budget 은 정본 빌더에서 그대로 가져온다). 배정 **전** 에 풀 전체에 건다.
4. **action 타입 최대한 균등** — 워터필링: 균등 몫보다 재고가 적은 타입(long_press /
   navigate_home) 은 풀 전량을 쓰고 남은 몫을 나머지 타입에 재분배한다. 앱 편중을
   막으려 앱 라운드로빈(안쪽은 에피소드 라운드로빈) 순서로 뽑는다.

복사 필터는 걸지 않는다 — 타깃이 state 가 아니라 action 이라 UNCHANGED 비율이 의미가
없고, 원천 자체가 이미 ``nocopy`` 필터본이다 (sidecar 에 명시한다).

stage1 5 파일 불가침
--------------------
``stage1_train.jsonl`` + ``stage1_test_*.jsonl`` 4 종은 학습·평가가 끝난 불가침 자산이다.
``build_exp08_stage2_v2`` 의 sha256 가드(``snapshot_stage1`` / ``compare_stage1``)를
그대로 재사용해 빌드 전후를 대조하고, 스냅샷을 sidecar 에 남겨 ``--verify-only`` 가
재해시로 다시 확인한다.

Usage
-----
  python scripts/build_exp08_inverse_mix_data.py
  python scripts/build_exp08_inverse_mix_data.py --verify-only

conda env ``implicit-world-modeling`` 의 python 으로 실행한다 (transformers/PIL 필요).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

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
    RATIO_DROPPED,
    RATIO_FULL,
    RATIO_MASKED,
    _abort,
    _action_of_gpt,
    _episode_roundrobin,
    _msg,
    _write_jsonl,
    attach_uniform_weights,
    filter_pool_by_length,
    remap_image,
)
from build_exp08_stage2_v2 import (  # noqa: E402
    STAGE1_IMMUTABLE,
    _sha256,
    compare_stage1,
    snapshot_stage1,
)

# ── 상수 ─────────────────────────────────────────────────────────────────────
SRC_INVERSE = "EXP08_stage1_inverse.jsonl"
SRC_META = "episodes_meta.jsonl"
SRC_DEFAULT_SUBDIR = "AndroidControl"
OUT_SUBDIR = "AndroidControl_EXP08"
PARENT = "stage1_train.jsonl"
OUT_NAME = "stage1_train_inverse_mix.jsonl"

N_FORWARD = 30000
N_INVERSE = 10000
N_ACTION = 10000

# 3-포맷 목표치 — 정본 빌더의 비율(RATIO_*)을 forward 총량에 그대로 건다.
FMT_RATIO = {"full": RATIO_FULL, "masked": RATIO_MASKED, "dropped": RATIO_DROPPED}

MODE_EXPECTED = "MID_ACTION_PREDICTION"
_MODE_RE = re.compile(r"^# Mode: (\S+)")
_VENDOR_IMG_RE = re.compile(r"^myset/images/episode_(\d+)_step_(\d+)\.jpg$")


# ── 부모 / 원천 로딩 ─────────────────────────────────────────────────────────


def load_parent(path: Path) -> tuple[list[dict], list[dict], set[str]]:
    """부모 stage1_train 을 (forward 40K, action 10K, 라인 sha256 집합) 으로 읽는다.

    라인 해시 집합은 "``task`` 를 빼면 부모 라인과 바이트 동일" 자체 검사의 기준이다.
    """
    fwd: list[dict] = []
    act: list[dict] = []
    hashes: set[str] = set()
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            hashes.add(hashlib.sha256(line.encode()).hexdigest())
            rec = json.loads(line)
            (fwd if "fmt" in rec else act).append(rec)
    return fwd, act, hashes


def _line_hash(rec: dict) -> str:
    """``_write_jsonl`` 이 쓸 라인의 sha256 (같은 json.dumps 설정)."""
    return hashlib.sha256(
        (json.dumps(rec, ensure_ascii=False) + "\n").encode()
    ).hexdigest()


def load_episode_apps(path: Path) -> dict[int, str]:
    apps: dict[int, str] = {}
    with path.open() as f:
        for line in f:
            r = json.loads(line)
            app = r.get("primary_app")
            if app:
                apps[int(r["episode_id"])] = app
    return apps


def load_indomain_apps(meta_path: Path) -> tuple[set[str], dict]:
    """``APP_BOTH ∪ APP_S1_ONLY``. 앱 파티션을 **재계산하지 않는다** — meta 가 정본."""
    meta = json.loads(meta_path.read_text())
    lists = meta.get("app_lists")
    if not lists or not {"APP_BOTH", "APP_S1_ONLY", "APP_OOD", "APP_S2_ONLY"} <= set(lists):
        _abort(f"{meta_path.name}::app_lists 가 없거나 4 그룹이 아니다 (앱 파티션 정본)")
    both, s1o = set(lists["APP_BOTH"]), set(lists["APP_S1_ONLY"])
    src = {
        "source": str(meta_path),
        "source_sha256": _sha256(meta_path),
        "definition": "APP_BOTH ∪ APP_S1_ONLY",
        "n_app_both": len(both),
        "n_app_s1_only": len(s1o),
        "n_indomain": len(both | s1o),
        "n_excluded": len(set(lists["APP_OOD"]) | set(lists["APP_S2_ONLY"])),
    }
    return both | s1o, src


def load_test_episodes(out_dir: Path) -> tuple[set[int], list[str]]:
    """EXP08 의 **모든** test 파일에 등장하는 에피소드 집합 (stage1 4 + stage2 8)."""
    files = sorted(p.name for p in out_dir.glob("stage1_test_*.jsonl")) + sorted(
        p.name for p in out_dir.glob("stage2_test*.jsonl")
    )
    if not files:
        _abort(f"{out_dir} 에 test 파일이 없다 — 누출 차단 근거를 만들 수 없다")
    eps: set[int] = set()
    for name in files:
        with (out_dir / name).open() as f:
            for line in f:
                eps.add(int(_EP_RE.search(json.loads(line)["images"][0]).group(1)))
    return eps, files


# ── inverse 풀 ───────────────────────────────────────────────────────────────


def build_inverse_pool(src: Path, ep2app, test_eps, indomain) -> tuple[list[tuple], dict]:
    """벤더 파일 → ``[(rec, action, episode, app)]`` 자격 풀 + 탈락 사유 카운트."""
    pool: list[tuple] = []
    reasons: Counter = Counter()
    unknown_eps: set[int] = set()
    with src.open() as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            rec = json.loads(line)
            if sorted(rec) != ["images", "messages"]:
                _abort(f"{src.name}:{i} 예상 밖 키 {sorted(rec)} (['images','messages'] 기대)")
            if len(rec["images"]) != 1:
                _abort(f"{src.name}:{i} images {len(rec['images'])}개 (1 기대)")
            mode = _MODE_RE.match(_msg(rec, "system"))
            if not mode or mode.group(1) != MODE_EXPECTED:
                _abort(f"{src.name}:{i} Mode 가 {MODE_EXPECTED} 가 아니다: {mode and mode.group(1)!r}")
            m = _VENDOR_IMG_RE.match(rec["images"][0])
            if not m:
                _abort(f"{src.name}:{i} 이미지 경로 패턴 밖: {rec['images'][0]!r}")
            ep = int(m.group(1))
            act = _action_of_gpt(rec, src.name, i)
            app = ep2app.get(ep)
            if app is None:
                reasons["app_unknown"] += 1
                unknown_eps.add(ep)
                continue
            if ep in test_eps:
                reasons["test_episode"] += 1
                continue
            if app not in indomain:
                reasons["app_not_indomain"] += 1
                continue
            reasons["eligible"] += 1
            pool.append((rec, act, ep, app))
    stats = {
        "source": str(src),
        "source_sha256": _sha256(src),
        "n_source_rows": sum(reasons.values()),
        "reasons": dict(sorted(reasons.items())),
        "app_unknown_episodes": sorted(unknown_eps),
    }
    return pool, stats


def balanced_alloc(counts: dict[str, int], target: int) -> dict[str, int]:
    """워터필링 균등 배분 — 재고가 균등 몫에 못 미치는 층은 전량, 남는 몫은 재분배.

    "가능한 한 균등, 부족분은 재분배" (희소 타입 long_press / navigate_home 은 풀 전량).
    나머지 1 씩은 재고가 많은 층부터 (동률은 이름순) — seed 와 무관하게 결정적이다.
    """
    if sum(counts.values()) < target:
        _abort(f"inverse: 재고 {sum(counts.values())} < 목표 {target}")
    alloc = dict.fromkeys(counts, 0)
    pending = sorted(counts)
    remaining = target
    while pending:
        share = remaining // len(pending)
        capped = [t for t in pending if counts[t] <= share]
        if not capped:
            for t in pending:
                alloc[t] = share
            rest = remaining - share * len(pending)
            for t in sorted(pending, key=lambda t: (-counts[t], t))[:rest]:
                alloc[t] += 1
            break
        for t in capped:
            alloc[t] = counts[t]
            remaining -= counts[t]
            pending.remove(t)
    if sum(alloc.values()) != target:
        _abort(f"inverse: 배분 합 {sum(alloc.values())} != 목표 {target}")
    return alloc


def order_by_app_then_episode(items: list[tuple], rng: random.Random) -> list[tuple]:
    """앱 라운드로빈 (앱 안에서는 에피소드 라운드로빈) — 한 앱/에피소드의 독점을 막는다."""
    by_app: dict[str, list[tuple]] = defaultdict(list)
    for it in items:
        by_app[it[3]].append(it)
    inner = {
        app: _episode_roundrobin(lst, rng, ep_of=lambda x: str(x[2]))
        for app, lst in sorted(by_app.items())
    }
    apps = sorted(inner)
    rng.shuffle(apps)
    pos = dict.fromkeys(apps, 0)
    out: list[tuple] = []
    remaining = sum(len(v) for v in inner.values())
    while remaining > 0:
        for app in apps:
            if pos[app] < len(inner[app]):
                out.append(inner[app][pos[app]])
                pos[app] += 1
                remaining -= 1
    return out


def select_inverse(pool: list[tuple], target: int, rng: random.Random) -> tuple[list[tuple], dict]:
    by_act: dict[str, list[tuple]] = defaultdict(list)
    for t in pool:
        by_act[t[1]].append(t)
    marginal = {a: len(v) for a, v in sorted(by_act.items())}
    alloc = balanced_alloc(marginal, target)
    selected: list[tuple] = []
    for act in sorted(by_act):
        selected.extend(order_by_app_then_episode(by_act[act], rng)[: alloc[act]])
    if len(selected) != target:
        _abort(f"inverse: 표본 {len(selected)} != 목표 {target}")
    realized = Counter(t[1] for t in selected)
    return selected, {
        "target": target,
        "rule": "워터필링 균등 배분 (재고 < 균등몫인 타입은 전량, 남는 몫은 재분배)",
        "pool_marginal": marginal,
        "alloc_target": dict(sorted(alloc.items())),
        "realized": dict(sorted(realized.items())),
        "n_apps": len({t[3] for t in selected}),
        "n_episodes": len({t[2] for t in selected}),
        "max_rows_per_app": max(Counter(t[3] for t in selected).values()),
        "max_rows_per_episode": max(Counter(t[2] for t in selected).values()),
    }


# ── 이미지 ───────────────────────────────────────────────────────────────────


def assert_images_resolve(records: list[dict], media_dir: Path, *, label: str) -> int:
    """선별 레코드의 이미지가 **전부** 디스크에 있는지 전수 확인. 한 건이라도 실패 → abort."""
    missing = [
        ip for r in records for ip in r["images"] if not (media_dir / ip).is_file()
    ]
    if missing:
        _abort(
            f"{label}: 이미지 {len(missing)}건 해석 실패 (예: {missing[:3]}). "
            "zero-pad remap 규칙이 이 원천에 맞는지 확인하라"
        )
    n = sum(len(r["images"]) for r in records)
    print(f"[images] {label}: {n}건 전수 해석 OK ({media_dir})")
    return n


# ── 빌드 ─────────────────────────────────────────────────────────────────────


def build(args: argparse.Namespace) -> dict:
    out_dir = args.data_root / OUT_SUBDIR
    rng = random.Random(args.seed)

    before = snapshot_stage1(out_dir)

    # ── 1. 부모 (forward / action) ───────────────────────────────────────────
    fwd_all, act_all, parent_hashes = load_parent(out_dir / PARENT)
    print(f"[parent] forward(fmt 보유)={len(fwd_all)}  action(fmt 부재)={len(act_all)}")
    if len(act_all) != args.n_action:
        _abort(f"부모 action {len(act_all)} != 목표 {args.n_action} (전량 사용 전제가 깨졌다)")

    by_fmt: dict[str, list[dict]] = defaultdict(list)
    for r in fwd_all:
        by_fmt[r["fmt"]].append(r)
    fmt_target = {f: round(args.n_forward * FMT_RATIO[f]) for f in sorted(FMT_RATIO)}
    if sum(fmt_target.values()) != args.n_forward:
        _abort(f"fmt 목표 합 {sum(fmt_target.values())} != {args.n_forward}")
    fwd_sel: list[dict] = []
    for fmt in sorted(fmt_target):
        stock = by_fmt.get(fmt, [])
        k = fmt_target[fmt]
        if len(stock) < k:
            _abort(f"forward {fmt}: 재고 {len(stock)} < 목표 {k}")
        idx = sorted(rng.sample(range(len(stock)), k))
        fwd_sel.extend(stock[i] for i in idx)
        print(f"[forward] {fmt}: 재고 {len(stock)} → {k}")

    # ── 2. inverse 풀 → 길이 필터 → 균등 배분 ────────────────────────────────
    ep2app = load_episode_apps(args.source_dir / SRC_META)
    indomain, app_src = load_indomain_apps(out_dir / "stage2_train.jsonl.meta.json")
    test_eps, test_files = load_test_episodes(out_dir)
    print(f"[leak-guard] test 파일 {len(test_files)}종 → 제외 에피소드 {len(test_eps)}")
    print(f"[app-guard] in-domain 앱 {len(indomain)} ({app_src['definition']})")

    pool, pool_stats = build_inverse_pool(
        args.source_dir / SRC_INVERSE, ep2app, test_eps, indomain)
    print(f"[inverse] 원천 {pool_stats['n_source_rows']} → 자격 풀 {len(pool)} "
          f"{pool_stats['reasons']}")

    media_dir = args.data_root
    len_drop = 0
    if not args.skip_length_filter:
        from filter_long_samples import build_length_fn  # noqa: PLC0415
        from transformers import AutoProcessor  # noqa: PLC0415

        proc = AutoProcessor.from_pretrained(
            args.model, revision=args.revision or None, trust_remote_code=True)
        length_of = build_length_fn(
            proc, image_max_pixels=IMG_MAX_PIXELS, image_min_pixels=IMG_MIN_PIXELS)
        pool, len_drop = filter_pool_by_length(
            pool, length_of, media_dir, CUTOFF_LEN, label="inverse")

    inv_sel, inv_meta = select_inverse(pool, args.n_inverse, rng)

    # ── 3. inverse: 이미지 remap → 전수 확인 → 균일 1.0 가중치 ───────────────
    inv_src_recs = [t[0] for t in inv_sel]
    inv_remapped = [
        {**r, "images": [remap_image(ip) for ip in r["images"]]} for r in inv_src_recs
    ]
    n_img_checked = assert_images_resolve(inv_remapped, media_dir, label="inverse")
    inv_weighted = attach_uniform_weights(inv_remapped, args.model)

    # inverse 원문 보존 자체 검사 — messages 원문 동일, images 는 remap, 추가 키는 둘뿐.
    for src_rec, out_rec in zip(inv_src_recs, inv_weighted):
        if out_rec["messages"] != src_rec["messages"]:
            _abort("inverse: messages 가 벤더 원문과 다르다")
        if out_rec["images"] != [remap_image(ip) for ip in src_rec["images"]]:
            _abort("inverse: images 가 remap 결과와 다르다")
        if set(out_rec) - set(src_rec) != {"token_weights"}:
            _abort(f"inverse: 예상 밖 추가 키 {sorted(set(out_rec) - set(src_rec))}")
        if not out_rec["token_weights"] or set(out_rec["token_weights"]) != {1.0}:
            _abort("inverse: token_weights 가 균일 1.0 이 아니다")

    # ── 4. task 라벨 부착 + 부모 바이트 동일 자체 검사 ───────────────────────
    rows: list[dict] = []
    for recs, task in ((fwd_sel, "forward"), (inv_weighted, "inverse"), (act_all, "action")):
        for r in recs:
            rows.append({**r, "task": task})
    parent_rows = [r for r in rows if r["task"] in ("forward", "action")]
    bad = sum(
        1 for r in parent_rows
        if _line_hash({k: v for k, v in r.items() if k != "task"}) not in parent_hashes
    )
    if bad:
        _abort(f"forward/action {bad}행이 부모 라인과 바이트 동일하지 않다")
    print(f"[byte-check] forward+action {len(parent_rows)}행: task 제거 시 부모 라인과 동일")

    if any("token_weights" not in r for r in rows):
        _abort("token_weights 없는 행이 있다 (stage1 YAML 은 diff token weighted loss 를 켠다)")

    rng.shuffle(rows)
    out_path = out_dir / OUT_NAME
    _write_jsonl(out_path, rows)

    # ── 5. sidecar ───────────────────────────────────────────────────────────
    after = snapshot_stage1(out_dir)
    fails = compare_stage1(before, after, label="stage1_immutable")
    if fails:
        for f in fails:
            print(f"[FAIL] {f}")
        _abort("stage1 불가침 5 파일이 빌드 중 변경됐다")
    print(f"[immutable] stage1 {len(STAGE1_IMMUTABLE)}파일 sha256 빌드 전후 동일")

    fwd_given = Counter()
    for r in fwd_sel:
        found = re.findall(r'"action"\s*:\s*"([a-z_]+)"', _msg(r, "human"))
        fwd_given[found[0] if found else "<unparsed>"] += 1

    meta = {
        "builder": "scripts/build_exp08_inverse_mix_data.py",
        "parent": str(out_dir / PARENT),
        "parent_sha256": before[PARENT]["sha256"],
        "seed": args.seed,
        "model": args.model,
        "revision_arg": args.revision,
        "tokenizer_note": (
            "attach_uniform_weights 는 revision 을 받지 않는다 (정본 함수 그대로 재사용). "
            "빌드 시점 로컬 스냅샷이 pinned revision 하나뿐임을 확인했다 — 균일 1.0 이라 "
            "값은 무관하지만 길이는 토크나이저에 의존한다."
        ),
        "composition": {
            "forward": args.n_forward, "inverse": args.n_inverse,
            "action": args.n_action, "total": len(rows),
            "ratio": "6 : 2 : 2",
        },
        "task_key": {
            "values": ["forward", "inverse", "action"],
            "why": (
                "기존 'fmt 키 부재 ⇒ action' 규약은 inverse 가 같은 파일에 들어오는 "
                "순간 에러 없이 깨진다 — 모든 행에 task 를 명시한다."
            ),
        },
        "forward": {
            "source": "부모의 fmt 보유 40K 서브샘플 (레코드 원문 그대로 + task)",
            "fmt_ratio": FMT_RATIO,
            "fmt_target": fmt_target,
            "fmt_stock": {f: len(by_fmt[f]) for f in sorted(by_fmt)},
            "given_action_realized": dict(sorted(fwd_given.items())),
        },
        "action": {
            "source": "부모의 fmt 부재 10K 전량 (레코드 원문 그대로 + task)",
            "n_rows": len(act_all),
        },
        "inverse": {
            **pool_stats,
            "mode": MODE_EXPECTED,
            "image_remap": (
                "myset/images/episode_{N}_step_{M}.jpg → "
                "AndroidControl/images/episode_{N:06d}_step_{M}.jpg "
                "(build_exp08_data::remap_image 재사용, 선별분 전수 확인)"
            ),
            "n_images_verified": n_img_checked,
            "pool_after_length_filter": len(pool),
            "token_weights": "균일 1.0 (attach_uniform_weights) — 타깃이 action 이라 diff 가중치가 없다",
            "app_partition": app_src,
            "leak_guard": {
                "test_files": test_files,
                "n_test_episodes": len(test_eps),
                "rule": "test 파일에 등장하는 에피소드를 통째로 제외 (step 단위 제외로는 누출이 남는다)",
            },
            "action_balance": inv_meta,
        },
        "length_filter": {
            "cutoff_len": CUTOFF_LEN,
            "image_max_pixels": IMG_MAX_PIXELS,
            "image_min_pixels": IMG_MIN_PIXELS,
            "inverse_dropped": len_drop,
            "skipped": bool(args.skip_length_filter),
            "note": "forward/action 은 부모 빌드에서 이미 같은 필터를 통과했다",
        },
        "copy_filter": {
            "applied": False,
            "reason": (
                "타깃이 state 가 아니라 action 이라 UNCHANGED 비율이 의미가 없다. "
                "원천 자체가 nocopy 필터본이다 (빠뜨린 것이 아니라 의도적 미적용)."
            ),
        },
        "step_overlap_with_parent": {},  # 아래에서 채운다
        "stage1_immutable": before,
        "files": {OUT_NAME: {"n_rows": len(rows)}},
    }

    # 부모 stage1 과의 step 중복 — inverse-dynamics 공동학습의 설계 의도(입력이 다르다)지
    # 누출이 아니다. 기록이 없으면 리뷰어가 사고로 읽는다. test 교집합 0 이 누출 0 의 증거.
    inv_keys = {r["images"][0] for r in inv_weighted}
    fwd_keys = {r["images"][0] for r in fwd_all}
    act_keys = {r["images"][0] for r in act_all}
    test_keys: set[str] = set()
    for name in test_files:
        with (out_dir / name).open() as f:
            for line in f:
                test_keys.add(json.loads(line)["images"][0])
    meta["step_overlap_with_parent"] = {
        "inverse_vs_stage1_forward_steps": len(inv_keys & fwd_keys),
        "inverse_vs_stage1_action_steps": len(inv_keys & act_keys),
        "inverse_vs_test_steps": len(inv_keys & test_keys),
        "note": (
            "forward/action 과의 중복은 같은 화면을 다른 입력·다른 타깃으로 학습하는 "
            "설계 의도다. test 교집합 0 이 eval 누출 0 의 증거."
        ),
    }
    print(f"[overlap] inverse ∩ stage1 forward={meta['step_overlap_with_parent']['inverse_vs_stage1_forward_steps']} "
          f"action={meta['step_overlap_with_parent']['inverse_vs_stage1_action_steps']} "
          f"test={meta['step_overlap_with_parent']['inverse_vs_test_steps']}")
    if meta["step_overlap_with_parent"]["inverse_vs_test_steps"]:
        _abort("inverse 가 test step 을 포함한다 (eval 누출)")

    meta_path = out_dir / (OUT_NAME + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    print(f"[write] {meta_path}")
    return meta


# ── 검증 ─────────────────────────────────────────────────────────────────────


def verify(out_dir: Path, data_root: Path, meta: dict) -> int:
    """산출물 불변식 검사. 0 = OK. ``--verify-only`` 는 sidecar 만으로 이걸 돌린다."""
    fails: list[str] = []
    out_path = out_dir / OUT_NAME
    if not out_path.exists():
        print(f"[FAIL] 산출물 없음: {out_path}")
        return 1

    rows = []
    with out_path.open() as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))

    want_total = meta["composition"]["total"]
    if len(rows) != want_total:
        fails.append(f"행수 {len(rows)} != {want_total}")

    task_dist = Counter(r.get("task", "<none>") for r in rows)
    print(f"[verify] task 분포: {dict(sorted(task_dist.items()))}")
    for t in ("forward", "inverse", "action"):
        if task_dist[t] != meta["composition"][t]:
            fails.append(f"task={t}: {task_dist[t]} != {meta['composition'][t]}")
    if set(task_dist) - {"forward", "inverse", "action"}:
        fails.append(f"예상 밖 task 값 {sorted(set(task_dist) - {'forward', 'inverse', 'action'})}")

    fmt_dist = Counter(r["fmt"] for r in rows if r.get("task") == "forward")
    print(f"[verify] forward fmt 분포: {dict(sorted(fmt_dist.items()))}")
    for fmt, want in meta["forward"]["fmt_target"].items():
        if fmt_dist[fmt] != want:
            fails.append(f"forward fmt={fmt}: {fmt_dist[fmt]} != {want}")
    stray = sum(1 for r in rows if r.get("task") != "forward" and "fmt" in r)
    if stray:
        fails.append(f"forward 가 아닌데 fmt 키를 가진 행 {stray}")

    no_w = sum(1 for r in rows if "token_weights" not in r)
    if no_w:
        fails.append(f"token_weights 없는 행 {no_w}")

    bad_prefix = sum(
        1 for r in rows if any(not ip.startswith("AndroidControl/") for ip in r.get("images", []))
    )
    if bad_prefix:
        fails.append(f"images prefix 위반 {bad_prefix}행")
    missing = [ip for r in rows for ip in r.get("images", []) if not (data_root / ip).is_file()]
    print(f"[verify] 이미지 전수 해석: {sum(len(r.get('images', [])) for r in rows)}건 중 미해결 {len(missing)}")
    if missing:
        fails.append(f"이미지 해석 실패 {len(missing)}건 (예: {missing[:3]})")

    # 부모 바이트 동일 — task 를 빼면 부모 라인과 같아야 한다.
    parent = Path(meta["parent"])
    if parent.exists():
        with parent.open() as f:
            phash = {hashlib.sha256(line.encode()).hexdigest() for line in f if line.strip()}
        bad = sum(
            1 for r in rows if r.get("task") in ("forward", "action")
            and _line_hash({k: v for k, v in r.items() if k != "task"}) not in phash
        )
        print(f"[verify] forward+action byte-identity: 불일치 {bad}행")
        if bad:
            fails.append(f"forward/action {bad}행이 부모 라인과 바이트 동일하지 않다")
    else:
        fails.append(f"부모 없음: {parent}")

    # inverse 구조 + 누출/도메인 재확인
    inv = [r for r in rows if r.get("task") == "inverse"]
    inv_keys = {ip for r in inv for ip in r["images"]}
    bad_keys = sum(1 for r in inv if sorted(r) != ["images", "messages", "task", "token_weights"])
    if bad_keys:
        fails.append(f"inverse: 예상 밖 키 구성 {bad_keys}행")
    bad_w = sum(1 for r in inv if set(r["token_weights"]) != {1.0})
    if bad_w:
        fails.append(f"inverse: 균일 1.0 이 아닌 token_weights {bad_w}행")
    def _mode_of(rec: dict) -> str | None:
        m = _MODE_RE.match(_msg(rec, "system"))
        return m.group(1) if m else None

    bad_mode = sum(1 for r in inv if _mode_of(r) != MODE_EXPECTED)
    if bad_mode:
        fails.append(f"inverse: Mode 가 {MODE_EXPECTED} 아닌 행 {bad_mode}")

    inv_act = Counter()
    for i, r in enumerate(inv):
        inv_act[_action_of_gpt(r, OUT_NAME, i)] += 1
    print(f"[verify] inverse action 분포: {dict(sorted(inv_act.items(), key=lambda x: -x[1]))}")
    if dict(sorted(inv_act.items())) != meta["inverse"]["action_balance"]["realized"]:
        fails.append("inverse action 분포가 sidecar 의 realized 와 다르다")

    test_eps: set[int] = set()
    test_keys: set[str] = set()
    for name in meta["inverse"]["leak_guard"]["test_files"]:
        with (out_dir / name).open() as f:
            for line in f:
                img = json.loads(line)["images"][0]
                test_keys.add(img)
                test_eps.add(int(_EP_RE.search(img).group(1)))
    leaked_ep = sum(
        1 for r in inv if int(_EP_RE.search(r["images"][0]).group(1)) in test_eps
    )
    print(f"[verify] inverse ∩ test 에피소드 {leaked_ep}행 / ∩ test step {len(inv_keys & test_keys)}건")
    if leaked_ep or (inv_keys & test_keys):
        fails.append(f"eval 누출: test 에피소드 {leaked_ep}행 / test step {len(inv_keys & test_keys)}건")

    lists = json.loads(Path(meta["inverse"]["app_partition"]["source"]).read_text())["app_lists"]
    indomain = set(lists["APP_BOTH"]) | set(lists["APP_S1_ONLY"])
    ep2app = load_episode_apps(data_root / "AndroidControl" / "episodes_meta.jsonl")
    ood = sum(
        1 for r in inv
        if ep2app.get(int(_EP_RE.search(r["images"][0]).group(1))) not in indomain
    )
    print(f"[verify] inverse in-domain 앱 위반 {ood}행 (APP_BOTH ∪ APP_S1_ONLY = {len(indomain)})")
    if ood:
        fails.append(f"inverse: in-domain 아닌 앱 {ood}행 (OOD 앱은 stage1 train 에 없어야 한다)")

    # stage1 5 파일 불가침 — sidecar 스냅샷과 재해시 대조 (mtime 은 보지 않는다:
    # 내용 불변이 불변식이고 mtime 변동은 거짓 실패다).
    for name in STAGE1_IMMUTABLE:
        want = meta["stage1_immutable"][name]["sha256"]
        got = _sha256(out_dir / name)
        if got != want:
            fails.append(f"stage1 불가침 위반: {name} sha256 {want} → {got}")
    print(f"[verify] stage1 불가침 {len(STAGE1_IMMUTABLE)}파일 sha256 sidecar 대조 완료")

    for x in fails:
        print(f"[FAIL] {x}")
    print("[verify] " + ("OK" if not fails else f"{len(fails)}건 실패"))
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-root", type=Path, default=PROJ / "data")
    p.add_argument("--source-dir", type=Path, default=PROJ / "data" / SRC_DEFAULT_SUBDIR,
                   help=f"{SRC_INVERSE} · {SRC_META} 가 있는 공유 원본 디렉토리")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--revision", default=DEFAULT_REVISION)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--n-forward", type=int, default=N_FORWARD)
    p.add_argument("--n-inverse", type=int, default=N_INVERSE)
    p.add_argument("--n-action", type=int, default=N_ACTION)
    p.add_argument("--skip-length-filter", action="store_true",
                   help="이미지가 없는 환경에서 임시 우회 (프로덕션 빌드에서는 쓰지 마라)")
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args(argv)

    out_dir = args.data_root / OUT_SUBDIR
    if args.verify_only:
        meta = json.loads((out_dir / (OUT_NAME + ".meta.json")).read_text())
        return verify(out_dir, args.data_root, meta)

    meta = build(args)
    return verify(out_dir, args.data_root, meta)


if __name__ == "__main__":
    raise SystemExit(main())
