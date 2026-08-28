#!/usr/bin/env python3
"""AC_EXP08 stage1 action-only 10K ablation 데이터 추출기 (M2 — World Modeling 순효과 측정).

메인 stage1(state 40K + action 10K)과 **정확히 같은 action 10K** 만으로 학습하는
stage1 full FT 대조군을 위한 데이터를 만든다. ``data/AndroidControl_EXP08/stage1_train.jsonl``
은 **읽기만** 한다 (수정·이동·삭제 금지) — 이건 재샘플링이 아니라 **필터링**이다:
그 파일에서 ``fmt`` 키가 없는 레코드(= downstream action 10K, ``build_exp08_data.py``
의 ``attach_uniform_weights`` 가 붙인 균일 1.0 ``token_weights``. state 40K 는 3-포맷
diff 가중치라 전부 ``fmt`` 키를 가진다)를 **파일에 나타난 순서 그대로, 원문 바이트
그대로** 뽑아 새 파일에 쓴다. 메인 stage1 이 실제로 학습에 쓴 것과 동일해야 이
ablation 이 성립한다 — 내용을 조금이라도 바꾸면 대조군의 전제가 깨진다.

산출 (``data/AndroidControl_EXP08/``):
  stage1_train_action_only.jsonl             10,000 행 (원문 라인 그대로)
  stage1_train_action_only.jsonl.meta.json    유래 sidecar (부모/필터규칙/행수/action 타입 분포)

Usage
-----
  python scripts/build_exp08_ablation_data.py
  python scripts/build_exp08_ablation_data.py --verify-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_exp08_data import _ACTION_RE, _msg  # noqa: E402

PROJ = Path(__file__).resolve().parent.parent
SRC = PROJ / "data" / "AndroidControl_EXP08" / "stage1_train.jsonl"
OUT = PROJ / "data" / "AndroidControl_EXP08" / "stage1_train_action_only.jsonl"
EXPECTED_N = 10000

FILTER_RULE = (
    "stage1_train.jsonl 의 레코드 중 'fmt' 키가 없는 것만 (= downstream action 10K, "
    "attach_uniform_weights 가 붙인 균일 1.0 token_weights). state 40K 는 3-포맷 "
    "diff 가중치라 전부 'fmt' 키를 갖는다. 재샘플링이 아니라 필터링 — 파일에 나타난 "
    "순서·내용을 원문 바이트 그대로 보존한다 (JSON 재직렬화하지 않는다)."
)


def _action_type(rec: dict) -> str:
    ms = _ACTION_RE.findall(_msg(rec, "gpt"))
    if len(ms) != 1:
        raise SystemExit(f"[ERROR] gpt <action> {len(ms)}개 (1 기대), images={rec.get('images')}")
    a = json.loads(ms[0])
    at = a.get("action")
    if not at:
        raise SystemExit(f"[ERROR] action type 없음, images={rec.get('images')}")
    return at


def extract(src: Path) -> tuple[list[str], list[dict]]:
    """``fmt`` 키가 없는 원문 라인을 순서 그대로 반환 (필터링, 재샘플링 아님)."""
    kept_lines: list[str] = []
    kept_recs: list[dict] = []
    with src.open() as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if "fmt" not in rec:
                kept_lines.append(line if line.endswith("\n") else line + "\n")
                kept_recs.append(rec)
    return kept_lines, kept_recs


def build(args: argparse.Namespace) -> dict:
    if not args.source.exists():
        raise SystemExit(f"[ERROR] 원본 없음: {args.source}")

    lines, recs = extract(args.source)
    if len(lines) != args.expected_n:
        raise SystemExit(
            f"[ERROR] 추출 행수 {len(lines)} != 기대 {args.expected_n} "
            "(stage1_train.jsonl 의 fmt 없는 레코드 수가 바뀌었다 — 원본을 확인하라)"
        )

    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    with tmp.open("w") as f:
        f.writelines(lines)
    tmp.replace(args.out)
    print(f"[write] {args.out}  ({len(lines)} 행)")

    dist = Counter(_action_type(r) for r in recs)

    meta = {
        "parent": str(args.source),
        "filter_rule": FILTER_RULE,
        "n_rows": len(lines),
        "action_type_distribution": dict(sorted(dist.items())),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
    }
    args.meta_out.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    print(f"[write] {args.meta_out}")
    return meta


def verify(out: Path, meta_path: Path, expected_n: int) -> int:
    fails: list[str] = []
    if not out.exists():
        fails.append(f"산출물 없음: {out}")
    else:
        n = 0
        fmt_leak = 0
        with out.open() as f:
            for line in f:
                if not line.strip():
                    continue
                n += 1
                if "fmt" in json.loads(line):
                    fmt_leak += 1
        if n != expected_n:
            fails.append(f"행수 {n} != 기대 {expected_n}")
        if fmt_leak:
            fails.append(f"fmt 키를 가진 행 {fmt_leak}개 (action-only 위반)")
    if not meta_path.exists():
        fails.append(f"sidecar 없음: {meta_path}")

    for x in fails:
        print(f"[FAIL] {x}")
    print("[verify] " + ("OK" if not fails else f"{len(fails)}건 실패"))
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", type=Path, default=SRC, help="읽기 전용 부모 파일 (수정 금지)")
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--expected-n", type=int, default=EXPECTED_N)
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args(argv)
    args.meta_out = args.out.with_name(args.out.name + ".meta.json")

    if args.verify_only:
        return verify(args.out, args.meta_out, args.expected_n)

    build(args)
    return verify(args.out, args.meta_out, args.expected_n)


if __name__ == "__main__":
    raise SystemExit(main())
