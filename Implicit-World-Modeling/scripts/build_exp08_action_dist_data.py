#!/usr/bin/env python3
"""AC_EXP08 stage2 ablation — action 분포를 stage1 downstream 에 맞춘 학습 데이터.

무엇을 왜
---------
stage2_train(30K) 은 **terminate 27.3%** 로 stage1 downstream(10K, terminate 14.9%) 과
분포가 크게 다르다. stage1 downstream 이 action 성능을 크게 끌어올렸으므로 그 분포를
그대로 가져가 본다 — 움직이는 변수를 "action 분포" 하나로 좁히는 대조군이다.

방법은 협업자 제안 그대로다: **원래 풀을 그대로 두고, 과대한 층(terminate·type)만 일부
드롭한 뒤 그 자리를 목표 분포에 맞는 새 샘플로 채운다.** 기존 샘플은 **바이트 불변**이라
"분포만 바뀌었다" 가 성립한다 (`stage1_train_action_only.jsonl` 이 부모 라인을 그대로
보존한 것과 같은 규약 — [AGENTS 하드 제약 15h](../AGENTS.md)).

⚠ 목표 분포에 **정확히는 도달할 수 없다** (실측 2026-09-08)
------------------------------------------------------------
메인 stage1 이 희소 action 을 이미 대부분 소비했다 — 소스의 `open` 5,386 중 3,532,
`navigate_back` 2,841 중 1,848 이 stage1 로 갔고 그건 동결이다. stage2 가 쓸 수 있는
전부는 `open` 1,854 / `navigate_back` 993 이고, 그중 현 30K 가 이미 1,267 / 660 을 쥐고
있어 **채울 수 있는 여유가 +99 / +119 뿐**이다.

  30K 목표:  open 1,878 · navigate_back 987   →  상한 1,366 · 779

그래서 이 빌더는 **규칙을 완화하지 않고 상한까지만 채우고 실현치를 sidecar 에 남긴다**
(이 저장소의 선례). 정확 일치를 원하면 N 을 21,800 까지 낮춰야 하는데, 그러면 분포와
**규모가 함께 움직여 비교 변수가 둘이 된다** — 사용자가 2026-09-08 에 근사(N≈28.4K)를 택했다.

eval 은 건드리지 않는다 — 그 라벨을 지키는 불변식
--------------------------------------------------
새로 채우는 샘플은 **현 `stage2_train` 의 에피소드 집합 안에서만** 뽑는다. 이 제약이
`action_test_s2_ood` 와 `action_test_ood` 의 "이 trajectory 는 stage2 train 에 없다" 를
유지시킨다 — 밖에서 뽑으면 그 라벨이 **에러 없이** 거짓이 된다. 물론 eval 11 파일의
step 자체도 전량 제외한다 (직접 누출).

남는 오차 하나: 드롭 때문에 일부 에피소드가 variant 에서 통째로 빠지면
`action_test_s2_id` 의 몇 행이 "라벨은 ID 인데 이 variant 는 안 봤다" 가 된다. 드롭을
**에피소드 라운드로빈의 뒤쪽부터** 하므로 최소화되고, 실제 건수는 sidecar 의
`eval_label_drift` 에 남는다.

Usage
-----
  python scripts/build_exp08_action_dist_data.py --dry-run
  python scripts/build_exp08_action_dist_data.py
  python scripts/build_exp08_action_dist_data.py --verify-only

conda env ``implicit-world-modeling`` 의 python 으로 실행한다.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
SCRIPTS = PROJ / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_exp08_data import (  # noqa: E402
    DEFAULT_SEED,
    _abort,
    _action_of_gpt,
    _episode_roundrobin,
    _read_jsonl,
    _write_jsonl,
    remap_image,
    remap_record,
)

SRC_DOWN = "EXP08_stage2.jsonl"
SRC_META = "episodes_meta.jsonl"
SRC_DEFAULT_SUBDIR = "AndroidControl"
OUT_SUBDIR = "AndroidControl_EXP08"

PARENT = "stage2_train.jsonl"
OUT_NAME = "stage2_train_action_distribution.jsonl"
SIDECAR = OUT_NAME + ".meta.json"

# 채움 후보에서 제외할 **메인 stage1 계보**. `stage1_train_inverse_mix.jsonl` 은 넣지 않는다 —
# 그것은 stage2_train **이후에** 만들어져 이미 2,538 step 을 공유하므로(실측), 제외하면
# 없는 제약을 새로 만드는 셈이 되고 희소 action 재고를 과소평가한다.
STAGE1_MAIN = ("stage1_train.jsonl", "stage1_train_action_only.jsonl")
EVAL_GLOBS = ("state_test_*.jsonl", "action_test_*.jsonl")

# 목표 = stage1 downstream(action-only) 10K 의 실현 분포.
# 정본은 `stage1_train.jsonl.meta.json::downstream_s1.realized` 이고, 빌더가 거기서 읽는다.
N_TARGET = 30000

_EP_STEP = re.compile(r"episode_0*(\d+)_step_0*(\d+)")


def _jsonl_lines(path: Path) -> list[str]:
    """jsonl 원문 라인. `splitlines()` 는 JSON 안의 유니코드 줄바꿈에서도 쪼갠다 — 쓰지 마라."""
    txt = path.read_text().split("\n")
    if txt and txt[-1] == "":
        txt.pop()
    return txt


def _step_key(rec: dict) -> str:
    img = rec["images"][0]
    return img if img.startswith("AndroidControl/") else remap_image(img)


def _ep_step(key: str) -> tuple[int, int]:
    m = _EP_STEP.search(key)
    if not m:
        _abort(f"step 키 파싱 실패: {key}")
    return int(m.group(1)), int(m.group(2))


def target_fractions(out_dir: Path) -> dict[str, float]:
    meta = json.loads((out_dir / "stage1_train.jsonl.meta.json").read_text())
    realized = meta["downstream_s1"]["realized"]
    total = sum(realized.values())
    return {a: n / total for a, n in realized.items()}


def load_exclusions(out_dir: Path) -> tuple[set[str], set[str]]:
    """(메인 stage1 step, eval 11 파일 step)."""
    s1: set[str] = set()
    for name in STAGE1_MAIN:
        p = out_dir / name
        if not p.exists():
            _abort(f"메인 stage1 train 이 없다: {p}")
        with p.open() as f:
            s1 |= {_step_key(json.loads(line)) for line in f}
    ev: set[str] = set()
    n_files = 0
    for pat in EVAL_GLOBS:
        for p in sorted(out_dir.glob(pat)):
            n_files += 1
            with p.open() as f:
                ev |= {_step_key(json.loads(line)) for line in f}
    if n_files != 11:
        _abort(f"eval 파일 11 개를 기대했으나 {n_files} 개 — eval v2 가 맞는지 확인하라")
    print(f"[exclude] 메인 stage1 {len(s1)} step · eval 11 파일 {len(ev)} step")
    return s1, ev


def build(args: argparse.Namespace) -> dict:
    out_dir = args.data_root / OUT_SUBDIR
    src_dir = args.source_dir
    rng = random.Random(args.seed)

    frac = target_fractions(out_dir)
    print("[target] stage1 downstream 비율: "
          + " ".join(f"{a}={frac[a]*100:.2f}%" for a in sorted(frac, key=lambda x: -frac[x])))

    # ── 1. 부모(현 stage2_train) ─────────────────────────────────────────────
    # ⚠ splitlines() 를 쓰지 마라 — JSON 문자열 안의 U+2028/U+2029·\x0b 등에서도 쪼개져
    # "Unterminated string" 으로 죽는다 (실측). 줄 구분은 "\n" 하나뿐이다.
    parent_lines = (out_dir / PARENT).read_text().split("\n")
    if parent_lines and parent_lines[-1] == "":
        parent_lines.pop()
    parent = [json.loads(l) for l in parent_lines]
    p_key = [_step_key(r) for r in parent]
    p_act = [_action_of_gpt(r, PARENT, i) for i, r in enumerate(parent)]
    cur = collections.Counter(p_act)
    parent_keys = set(p_key)
    parent_eps = {_ep_step(k)[0] for k in p_key}
    print(f"[parent] {len(parent)} 행 · 에피소드 {len(parent_eps)} · 분포 {dict(cur.most_common())}")

    # ── 2. 채움 후보 ─────────────────────────────────────────────────────────
    s1_steps, eval_steps = load_exclusions(out_dir)
    fills: dict[str, list[dict]] = collections.defaultdict(list)
    n_src = 0
    with (src_dir / SRC_DOWN).open() as f:
        for i, line in enumerate(f):
            rec = json.loads(line)
            key = _step_key(rec)
            n_src += 1
            if key in parent_keys or key in s1_steps or key in eval_steps:
                continue
            ep, _ = _ep_step(key)
            if ep not in parent_eps:      # ← OOD 라벨을 지키는 제약
                continue
            fills[_action_of_gpt(rec, SRC_DOWN, i)].append(rec)
    avail = {a: len(v) for a, v in fills.items()}
    print(f"[fill] 소스 {n_src} → 후보 {sum(avail.values())} · action별 {dict(sorted(avail.items(), key=lambda x: -x[1]))}")

    # ── 3. 쿼터 — 목표치를 재고 상한에서 자른다 ──────────────────────────────
    quota, shortfall = {}, {}
    for a in frac:
        want = round(args.n_target * frac[a])
        cap = cur[a] + avail.get(a, 0)
        quota[a] = min(want, cap)
        if want > cap:
            shortfall[a] = want - cap
    realized_n = sum(quota.values())
    print(f"[quota] 목표 N={args.n_target} → 실현 {realized_n}")
    for a in sorted(frac, key=lambda x: -frac[x]):
        mark = f"  ← 재고 부족 {shortfall[a]}" if a in shortfall else ""
        print(f"  {a:14s} 현재 {cur[a]:6d} + 채움 {min(max(quota[a]-cur[a],0), avail.get(a,0)):5d} "
              f"= {quota[a]:6d}  ({quota[a]/realized_n*100:5.2f}% / 목표 {frac[a]*100:5.2f}%){mark}")

    if args.dry_run:
        return {"dry_run": True, "realized_n": realized_n, "quota": quota, "shortfall": shortfall}

    # ── 4. 유지/드롭 — 에피소드 라운드로빈 앞쪽을 남겨 커버리지를 지킨다 ─────
    idx_by_act: dict[str, list[int]] = collections.defaultdict(list)
    for i, a in enumerate(p_act):
        idx_by_act[a].append(i)
    keep_idx: set[int] = set()
    for a, idxs in idx_by_act.items():
        k = min(quota.get(a, 0), len(idxs))
        ordered = _episode_roundrobin(sorted(idxs), rng, ep_of=lambda i: _ep_step(p_key[i])[0])
        keep_idx.update(ordered[:k])
    dropped = collections.Counter(p_act[i] for i in range(len(parent)) if i not in keep_idx)
    print(f"[keep] 유지 {len(keep_idx)} / 드롭 {len(parent)-len(keep_idx)} {dict(dropped.most_common())}")

    # ── 5. 채움 추출 ─────────────────────────────────────────────────────────
    ep2app: dict[int, str] = {}
    with (src_dir / SRC_META).open() as f:
        for line in f:
            r = json.loads(line)
            ep2app[int(r["episode_id"])] = r.get("primary_app")
    app_lists = json.loads((out_dir / "stage2_train.jsonl.meta.json").read_text())["app_lists"]
    app_of_bucket = {app: b for b, apps in app_lists.items() for app in apps}

    added: list[dict] = []
    add_count: collections.Counter = collections.Counter()
    for a, need in ((a, quota[a] - cur[a]) for a in quota):
        if need <= 0:
            continue
        pool = fills.get(a, [])
        ordered = _episode_roundrobin(pool, rng, ep_of=lambda r: _ep_step(_step_key(r))[0])
        for rec in ordered[:need]:
            rec = remap_record(dict(rec))
            ep, st = _ep_step(_step_key(rec))
            app = ep2app.get(ep)
            added.append({
                **rec,
                "bucket": "stage2_train",
                "primary_app": app,
                "app_bucket": app_of_bucket.get(app, "APP_BOTH"),
                "step_bucket": "s2_train",
                "s1_state_seen": False,   # 메인 stage1(state 포함)을 제외한 풀에서만 뽑았다
                "episode_id": ep,
                "step_id": st,
                "added_by": "action-distribution",
            })
            add_count[a] += 1
    print(f"[fill] 추가 {len(added)} {dict(add_count.most_common())}")

    # ── 6. 출력 — 유지분은 부모 **원문 라인 그대로** ─────────────────────────
    out_lines = [parent_lines[i] for i in sorted(keep_idx)]
    out_lines += [json.dumps(r, ensure_ascii=False) for r in added]
    rng.shuffle(out_lines)
    tmp = out_dir / (OUT_NAME + ".tmp")
    tmp.write_text("\n".join(out_lines) + "\n")
    tmp.replace(out_dir / OUT_NAME)
    print(f"[write] {out_dir / OUT_NAME}  ({len(out_lines)} 행)")

    # ── 7. sidecar ───────────────────────────────────────────────────────────
    kept_eps = {_ep_step(p_key[i])[0] for i in keep_idx} | {r["episode_id"] for r in added}
    drift = eval_label_drift(out_dir, parent_eps, kept_eps)
    res = {
        "builder": "scripts/build_exp08_action_dist_data.py",
        "built_at": args.stamp,
        "parent": PARENT,
        "seed": args.seed,
        "n_target": args.n_target,
        "n_realized": len(out_lines),
        "target_fractions": frac,
        "target_source": "stage1_train.jsonl.meta.json::downstream_s1.realized",
        "parent_distribution": dict(sorted(cur.items())),
        "quota": dict(sorted(quota.items())),
        "realized_distribution": dict(sorted(collections.Counter(
            [p_act[i] for i in keep_idx] + [a for a, n in add_count.items() for _ in range(n)]).items())),
        "shortfall_vs_target": shortfall,
        "shortfall_reason": (
            "메인 stage1 이 open/navigate_back 재고를 이미 소비했다 (open 5,386 중 3,532, "
            "navigate_back 2,841 중 1,848). stage2 가 쓸 수 있는 전부가 1,854/993 이라 "
            "30K 목표(1,878/987)를 채울 수 없다. 규칙을 완화하지 않고 상한까지만 채웠다."
        ),
        "dropped": dict(sorted(dropped.items())),
        "added": dict(sorted(add_count.items())),
        "fill_pool_available": dict(sorted(avail.items())),
        "fill_constraints": [
            "step ∉ 메인 stage1 (stage2 ∩ stage1 = 0 불변식)",
            "step ∉ 현 stage2_train (중복 방지)",
            "step ∉ eval 11 파일 (직접 누출 방지)",
            "episode ∈ 현 stage2_train 에피소드 집합 (s2_ood·ood 라벨 보존)",
        ],
        "episodes": {"parent": len(parent_eps), "variant": len(kept_eps),
                     "dropped": len(parent_eps - kept_eps)},
        "eval_label_drift": drift,
    }
    (out_dir / SIDECAR).write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"[write] {out_dir / SIDECAR}")
    return res


def eval_label_drift(out_dir: Path, parent_eps: set[int], variant_eps: set[int]) -> dict:
    """eval 라벨이 이 variant 에 대해 어긋나는 정도. s2_id 만 어긋날 수 있다."""
    out = {}
    for name, kind in (("action_test_s2_id.jsonl", "id"),
                       ("action_test_s2_ood.jsonl", "ood"),
                       ("action_test_ood.jsonl", "ood")):
        p = out_dir / name
        if not p.exists():
            continue
        eps = []
        with p.open() as f:
            for line in f:
                eps.append(_ep_step(_step_key(json.loads(line)))[0])
        if kind == "id":
            out[name] = {"rows": len(eps),
                         "labeled_id_but_unseen_by_variant": sum(1 for e in eps if e not in variant_eps)}
        else:
            out[name] = {"rows": len(eps),
                         "seen_by_variant": sum(1 for e in eps if e in variant_eps)}
    return out


def verify(out_dir: Path, res: dict, src_dir: Path) -> int:
    fails: list[str] = []
    recs = _read_jsonl(out_dir / OUT_NAME)
    if len(recs) != res["n_realized"]:
        fails.append(f"행수 {len(recs)} != {res['n_realized']}")
    keys = [_step_key(r) for r in recs]
    if len(set(keys)) != len(keys):
        fails.append(f"파일 내부 중복 step {len(keys)-len(set(keys))}")
    print(f"[verify] 행수 {len(recs)} · 중복 0")

    bad = sum(1 for r in recs if any(not i.startswith("AndroidControl/") for i in r.get("images", [])))
    if bad:
        fails.append(f"images prefix 위반 {bad}")
    print(f"[verify] images prefix 전수 {len(recs)} 행")

    s1_steps, eval_steps = load_exclusions(out_dir)
    n1 = len(set(keys) & s1_steps)
    n2 = len(set(keys) & eval_steps)
    print(f"[verify] ∩ 메인 stage1 = {n1} · ∩ eval 11 파일 = {n2}")
    if n1:
        fails.append(f"메인 stage1 과 교집합 {n1}")
    if n2:
        fails.append(f"eval 과 교집합 {n2} (직접 누출)")

    parent_eps = {_ep_step(_step_key(r))[0] for r in _read_jsonl(out_dir / PARENT)}
    new_eps = {_ep_step(k)[0] for k in keys} - parent_eps
    print(f"[verify] 부모 밖 에피소드 = {len(new_eps)} (0 이어야 OOD 라벨이 산다)")
    if new_eps:
        fails.append(f"부모 stage2_train 밖 에피소드 {len(new_eps)} — s2_ood·ood 라벨이 깨진다")

    # 유지분이 부모 라인과 바이트 동일한가 (분포만 바뀌었다는 주장의 근거)
    parent_lines = set(_jsonl_lines(out_dir / PARENT))
    kept = [l for l in _jsonl_lines(out_dir / OUT_NAME) if '"added_by"' not in l]
    not_identical = sum(1 for l in kept if l not in parent_lines)
    print(f"[verify] 유지분 {len(kept)} 행 중 부모와 바이트 동일하지 않은 행 = {not_identical}")
    if not_identical:
        fails.append(f"유지분 {not_identical} 행이 부모 라인과 다르다")

    got = collections.Counter(_action_of_gpt(r, OUT_NAME, i) for i, r in enumerate(recs))
    want = res["realized_distribution"]
    if dict(sorted(got.items())) != want:
        fails.append(f"분포 불일치 {dict(sorted(got.items()))} != {want}")
    print(f"[verify] 실현 분포 {dict(sorted(got.items()))}")
    tot = sum(got.values())
    print("[verify] 목표 대비: " + " ".join(
        f"{a}={got[a]/tot*100:.2f}%/{res['target_fractions'][a]*100:.2f}%"
        for a in sorted(res["target_fractions"], key=lambda x: -res["target_fractions"][x])))

    if fails:
        print("\n[verify] 실패:")
        for m in fails:
            print("  - " + m)
        return 1
    print("\n[verify] 전 항목 통과")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", type=Path, default=PROJ / "data")
    ap.add_argument("--source-dir", type=Path, default=None)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--n-target", type=int, default=N_TARGET)
    ap.add_argument("--stamp", default="2026-09-08")
    ap.add_argument("--dry-run", action="store_true", help="쿼터·실현 분포만 출력")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args(argv)
    if args.source_dir is None:
        args.source_dir = args.data_root / SRC_DEFAULT_SUBDIR

    out_dir = args.data_root / OUT_SUBDIR
    if args.verify_only:
        p = out_dir / SIDECAR
        if not p.exists():
            _abort(f"sidecar 없음: {p}")
        return verify(out_dir, json.loads(p.read_text()), args.source_dir)
    res = build(args)
    if args.dry_run:
        return 0
    return verify(out_dir, res, args.source_dir)


if __name__ == "__main__":
    raise SystemExit(main())
