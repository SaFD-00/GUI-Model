"""AC_EXP08 eval v2 배선 드리프트 가드 (2026-09-08 eval 전면 교체).

이 실험군의 eval 파일 이름은 **네 곳**에 나타난다 — 빌더 상수, eval 셸 두 개의 기본
목록, `dataset_info.json` 등록 키, 그리고 뷰어 엔트리. 하나만 어긋나도 실패가 조용하다:
셸은 "Missing test jsonl" 로 죽거나(그나마 시끄러움) **등록 키가 없으면 LlamaFactory 가
엉뚱한 데이터를 잡는다** (하드 제약 14 와 같은 유형). 그래서 셸 원문을 파싱해 정본과
대조한다 — `tests/test_pixel_xy_consistency.py` 가 쓰는 것과 같은 수법이다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

BUILDER = ROOT / "scripts" / "build_exp08_eval_v2.py"
STAGE1_SH = ROOT / "scripts" / "stage1_eval.sh"
STAGE2_SH = ROOT / "scripts" / "stage2_eval.sh"
DATASET_INFO = ROOT / "configs" / "lf_dataset" / "dataset_info.json"

# 정본 = 빌더가 굽는 파일 이름 (build_exp08_eval_v2.py 의 ACTION_BUCKETS/STATE_BUCKETS).
STATE_STEMS = [f"state_test_{b}_{f}" for b in ("id", "ood")
               for f in ("full", "masked", "dropped")]
ACTION_BUCKETS = ["s1_id", "s1_ood", "s2_id", "s2_ood", "ood"]
ACTION_STEMS = [f"action_test_{b}" for b in ACTION_BUCKETS]
ALL_STEMS = STATE_STEMS + ACTION_STEMS


def _builder_constants() -> tuple[list[str], list[str]]:
    """빌더에서 실제 파일명 튜플을 읽는다 (문자열 파싱이 아니라 import)."""
    import build_exp08_eval_v2 as b  # noqa: PLC0415

    return list(b.STATE_FILES), list(b.ACTION_FILES)


def test_builder_is_the_source_of_truth():
    state_files, action_files = _builder_constants()
    assert sorted(state_files) == sorted(f"{s}.jsonl" for s in STATE_STEMS)
    assert sorted(action_files) == sorted(f"{s}.jsonl" for s in ACTION_STEMS)


def test_stage1_eval_default_tasks_cover_every_file():
    """stage1_eval.sh 는 state 6 + action 5 = 11 leaf 를 기본으로 돈다 (사용자 결정 "양쪽 전량")."""
    txt = STAGE1_SH.read_text()
    m = re.search(r"for stem in \$\{EVAL_TASKS:-([^}]*)\}", txt)
    assert m, "stage1_eval.sh 에서 EVAL_TASKS 기본값을 찾지 못했다"
    stems = m.group(1).split()
    # stem → 파일 stem 변환 규칙이 셸과 같아야 한다: state_X → state_test_X, action_X → action_test_X
    files = [
        f"state_test_{s[len('state_'):]}" if s.startswith("state_")
        else f"action_test_{s[len('action_'):]}"
        for s in stems
    ]
    assert sorted(files) == sorted(ALL_STEMS), f"stage1 기본 leaf 불일치: {files}"


def test_stage2_eval_default_buckets_are_action_only():
    """stage2_eval.sh 는 action 5 버킷만 돈다 (state 는 stage1 만 학습하는 과제다)."""
    txt = STAGE2_SH.read_text()
    m = re.search(r"for bucket in \$\{EVAL_BUCKETS:-([^}]*)\}", txt)
    assert m, "stage2_eval.sh 에서 EVAL_BUCKETS 기본값을 찾지 못했다"
    assert sorted(m.group(1).split()) == sorted(ACTION_BUCKETS)


def test_eval_task_and_bucket_value_spaces_do_not_overlap():
    """값 공간이 겹치면 stage1→stage2 래퍼에서 값이 새어 엉뚱한 leaf 를 평가한다."""
    t = re.search(r"for stem in \$\{EVAL_TASKS:-([^}]*)\}", STAGE1_SH.read_text()).group(1).split()
    b = re.search(r"for bucket in \$\{EVAL_BUCKETS:-([^}]*)\}", STAGE2_SH.read_text()).group(1).split()
    assert not (set(t) & set(b))


@pytest.mark.parametrize("stem", ALL_STEMS)
def test_dataset_info_registers_every_eval_file(stem):
    """등록 키가 없으면 학습·추론 진입 전에 죽거나 조용히 잘못 돈다 (하드 제약 14)."""
    info = json.loads(DATASET_INFO.read_text())
    key = f"IWM-AC_EXP08_{stem}"
    assert key in info, f"{key} 가 dataset_info.json 에 없다"
    assert info[key]["file_name"] == f"../../data/AndroidControl_EXP08/{stem}.jsonl"


def test_archived_eval_keys_point_at_archived_files():
    """구 eval 은 날짜 접미로 아카이브됐다 — 등록 키와 경로가 함께 움직여야 재채점이 된다."""
    info = json.loads(DATASET_INFO.read_text())
    archived = {k: v for k, v in info.items() if k.startswith("IWM-AC_EXP08_") and "2026-08-" in k}
    assert len(archived) == 11, f"아카이브 키 11 개를 기대했으나 {len(archived)}"
    for k, v in archived.items():
        stamp = k.rsplit("_", 1)[1]
        assert v["file_name"].endswith(f"_{stamp}.jsonl"), f"{k} 경로가 날짜 접미를 안 가졌다"


def test_viewer_entries_match_shell_leaves():
    """뷰어가 읽는 디렉토리 이름이 셸이 만드는 leaf 와 같아야 표가 빈칸이 되지 않는다."""
    import eval_viewer as v  # noqa: PLC0415

    s1 = v._exp08_stage1_entries()
    s2 = v._exp08_stage2_entries()
    # 셸 leaf = stem 의 `_` → `-` (state_id_full → state-id-full)
    want_s1 = {f"on-AC_EXP08-state-{b}-{f}" for b in ("id", "ood")
               for f in ("full", "masked", "dropped")}
    want_s1 |= {f"on-AC_EXP08-action-{b.replace('_', '-')}" for b in ACTION_BUCKETS}
    got_s1 = {e["dir"] for e in s1.values() if not e["dir"].endswith("-without-open_app")}
    assert got_s1 == want_s1
    assert {e["dir"] for e in s2.values()} == {
        f"on-AC_EXP08-action-{b.replace('_', '-')}" for b in ACTION_BUCKETS
    }
    for e in list(s1.values()) + list(s2.values()):
        assert Path(e["test"]).parent.name == "AndroidControl_EXP08"
