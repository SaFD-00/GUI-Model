"""절대 픽셀 xy 채점 판정이 파이썬 정본과 셸에서 갈리지 않는지 강제한다.

왜 셸을 **파싱**하는가
----------------------
같은 판정("이 DS 는 pos/xy 로 채점한다")이 세 군데 복제됐고 두 번 갈렸다 —
rebuild_*.sh 3 종이 갈린 것이 1차(2026-08-03), 셸 통합이 언어 경계를 안 넘어
`_compare_site.XY_FAMILY` 가 AC_EXP08 을 놓친 것이 2차(2026-08-28)다. 경위는
`lf_registry.PIXEL_XY_DATASETS` 주석에 있다.

셸이 런타임에 파이썬을 호출해 정본을 읽게 만들 수도 있었지만(그 선례가
`_common.sh::require_model_eligible` 에 있다) 그러지 않았다 — `ds_is_pixel_xy` 는
eval 셸의 판정 경로라 호출마다 인터프리터를 기동하는 비용과 conda env 의존을
새로 지는 것이 드리프트 방지보다 비싸다. **의도적 트레이드오프**로 실행 경로는
그대로 두고, 대신 이 테스트가 셸 원문을 읽어 목록이 갈리는 순간 실패시킨다.

파싱이 깨질 때 조용하지 않게
----------------------------
정규식이 아무것도 못 잡으면 빈 집합끼리 비교해 **통과해버리는** 함정이 있다.
그래서 매치 0 건은 전부 명시적 실패로 처리한다 — 이 리포에서 같은 유형("조용한 0")
버그가 반복해서 나왔다.
"""

from __future__ import annotations

import importlib
import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
for _p in (_ROOT, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from implicit_world_modeling.lf_registry import (  # noqa: E402
    PIXEL_XY_DATASETS,
    pixel_xy_ds_keys,
)

_COMMON_SH = _SCRIPTS / "_common.sh"
_STAGE2_SH = _SCRIPTS / "stage2_eval.sh"
_XY_ASSIGN = 'action_mode_flag="--coord-mode xy"'
# 리터럴 대입을 정본 헬퍼 호출로 갈아치운 형태. `ds_score_mode_flag` 는 내부에서
# `ds_is_pixel_xy` 로 판정하므로 목록이 셸에 두 번 적히지 않는다.
_DELEGATED_ASSIGN_RE = re.compile(
    r'action_mode_flag="\$\(ds_score_mode_flag\s+"\$eval_ds"\s+action\)"'
)


def _parse_ds_is_pixel_xy(src: str) -> set[str]:
    """`_common.sh::ds_is_pixel_xy()` 의 `return 0` case arm 에서 DS 키를 뽑는다.

    함수 본문을 먼저 잘라내고 그 안에서만 찾는다 — 파일 전체에서 case arm 을 긁으면
    다른 함수의 목록(예: parse 허용목록)을 주워 담는다.
    """
    m = re.search(r"^ds_is_pixel_xy\(\)\s*\{(.*?)^\}", src, re.S | re.M)
    if m is None:
        raise AssertionError(
            "_common.sh 에서 ds_is_pixel_xy() 함수 본문을 찾지 못했다 — "
            "함수가 사라졌거나 이 테스트의 파서가 낡았다. 어느 쪽이든 "
            "채점 모드 판정이 무방비가 되므로 조용히 통과시키지 않는다."
        )
    arms = re.findall(r"^\s*([A-Za-z0-9_|]+)\)\s*return 0\s*;;", m.group(1), re.M)
    if not arms:
        raise AssertionError(
            "ds_is_pixel_xy() 본문에서 `return 0` case arm 을 하나도 못 뽑았다 "
            f"(본문: {m.group(1)!r})"
        )
    return {key for arm in arms for key in arm.split("|")}


def _parse_stage2_inline(src: str) -> tuple[str, set[str]]:
    """stage2_eval.sh 의 xy 플래그 가드를 읽는다 → (mode, keys).

    mode 는 셋 중 하나다:
      ``inline``    — `[[ "$eval_ds" == "AC_..." || ... ]]` 로 목록을 다시 적은 상태.
                      keys 가 정본과 같아야 한다.
      ``delegated`` — 정본 헬퍼로 대체된 상태 (바람직한 종착지). keys 없음. 두 형태를
                      인정한다 — 리터럴 대입을 `ds_is_pixel_xy` 로 감싼 것과, 대입 자체를
                      `ds_score_mode_flag` 반환값으로 바꾼 것 (2026-08-28 배선이 후자다).
      ``unknown``   — 둘 다 아님. 파서가 형태를 못 알아본 것이므로 **실패**시킨다.
    """
    idx = src.find(_XY_ASSIGN)
    if idx < 0:
        # 리터럴 대입이 아예 사라진 형태 — 정본 헬퍼에 위임됐을 때만 통과시킨다.
        # (`ds_score_mode_flag` 안에서 `ds_is_pixel_xy` 가 판정한다.)
        if _DELEGATED_ASSIGN_RE.search(src):
            return "delegated", set()
        return "unknown", set()
    guard = src[:idx].rsplit("if ", 1)[-1]  # 그 대입 직전의 if 절
    if "ds_is_pixel_xy" in guard:
        return "delegated", set()
    keys = set(re.findall(r'"\$eval_ds"\s*==\s*"([A-Za-z0-9_]+)"', guard))
    return ("inline", keys) if keys else ("unknown", set())


def _diff_msg(label_a: str, a: set[str], label_b: str, b: set[str]) -> str:
    return (
        f"{label_a} 와 {label_b} 가 갈렸다.\n"
        f"  {label_a} 에만: {sorted(a - b) or '없음'}\n"
        f"  {label_b} 에만: {sorted(b - a) or '없음'}\n"
        f"  {label_a}={sorted(a)}\n  {label_b}={sorted(b)}"
    )


class TestPixelXySsot(unittest.TestCase):
    def test_short_keys_derive_from_long_names(self):
        """정본은 긴 이름, 소비자는 짧은 키 — 치환이 전수 성립해야 한다."""
        self.assertTrue(PIXEL_XY_DATASETS, "PIXEL_XY_DATASETS 가 비었다")
        self.assertEqual(len(pixel_xy_ds_keys()), len(PIXEL_XY_DATASETS),
                         "짧은 키 변환에서 충돌이 났다 (서로 다른 DS 가 같은 키로)")
        for k in pixel_xy_ds_keys():
            self.assertTrue(k.startswith("AC_"), f"짧은 키 규약 위반: {k}")

    def test_compare_site_derives_not_duplicates(self):
        """`_compare_site.XY_FAMILY` 를 다시 하드코딩하면 여기서 걸린다."""
        cs = importlib.import_module("_compare_site")
        self.assertEqual(
            set(cs.XY_FAMILY), set(pixel_xy_ds_keys()),
            _diff_msg("XY_FAMILY", set(cs.XY_FAMILY),
                      "PIXEL_XY_DATASETS", set(pixel_xy_ds_keys())),
        )


class TestShellMatchesSsot(unittest.TestCase):
    def test_common_sh_ds_is_pixel_xy(self):
        shell = _parse_ds_is_pixel_xy(_COMMON_SH.read_text())
        ssot = set(pixel_xy_ds_keys())
        self.assertEqual(
            shell, ssot,
            _diff_msg("_common.sh::ds_is_pixel_xy", shell, "PIXEL_XY_DATASETS", ssot),
        )

    def test_stage2_eval_sh_inline_copy(self):
        """인라인 복제본은 정본과 같거나, `ds_is_pixel_xy` 위임으로 사라졌거나.

        "반드시 인라인이 존재" 로 단언하지 않는다 — 그 인라인을 위임 호출로 바꾸는
        정리가 예정돼 있고, 정리 때문에 실패하는 가드는 정리를 막는다. 드리프트만
        실패로 잡는다. (skip 은 쓰지 않는다 — "검사가 안 돌았다" 와 구분이 안 된다.)
        """
        mode, keys = _parse_stage2_inline(_STAGE2_SH.read_text())
        self.assertIn(
            mode, ("inline", "delegated"),
            f"stage2_eval.sh 의 `{_XY_ASSIGN}` 가드 형태를 알아보지 못했다 — "
            "배선이 바뀌었거나 이 테스트의 파서가 낡았다. 채점 모드 판정을 "
            "무방비로 두지 않기 위해 실패시킨다.",
        )
        if mode == "inline":
            ssot = set(pixel_xy_ds_keys())
            self.assertEqual(
                keys, ssot,
                _diff_msg("stage2_eval.sh 인라인", keys, "PIXEL_XY_DATASETS", ssot),
            )


if __name__ == "__main__":
    unittest.main()
