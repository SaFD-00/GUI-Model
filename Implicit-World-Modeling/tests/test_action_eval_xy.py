"""Regression tests for scripts/_action_eval.py --coord-mode xy (AndroidControl_EXP05).

EXP05 는 액션 스페이스가 xy 로 통일돼 index grounding 이 없다. 실 스키마는
index 모드(AndroidControl_3) 와 다르다 — 키가 `action_type` 이 아니라 `action`,
click 은 단일 `coordinate`, 방향 액션은 `swipe` 의 coordinate1→coordinate2:

  <action>{"action":"click","coordinate":[622,620]}</action>
  <action>{"action":"swipe","coordinate1":[420,1407],"coordinate2":[420,469]}</action>
  <action>{"action":"open","app_name":"Plantum"}</action>

채점 규칙 (2026-07-11 회의 확정):
  click/long_press : GT 좌표를 포함하는 최소 면적 element 의 bounds 안(경계 포함)에
                     pred 좌표가 들어가면 정답. 포함 element 부재 시 오답 + no_bbox_n 집계.
  swipe            : start→end 벡터의 주 성분 방향 일치 (|dx| >= |dy| → left/right, else up/down)
  type / open      : 좌표 무관. 텍스트 / app_name 매칭만.
  wait / navigate_*: 타입만 일치하면 통과.

`--xml-schema` (2026-08-28 추가) 는 위 bbox 채점이 읽을 **위치축 속성**만 고른다:
android (기본) 은 `bounds="[x1,y1][x2,y2]"`, cerebra 는 AC_EXP08 의
`data-bbox="x1 y1 x2 y2"`. 기본값으로 EXP08 을 채점하면 위치축을 하나도 못 읽어
**에러 없이** 전 건이 no_bbox 로 빠진다 (하드 제약 15f 와 같은 계열) — 아래
CerebraSchema 가 양방향으로 못박는다.

Run:
    pytest tests/test_action_eval_xy.py -v
    # or: python -m unittest tests.test_action_eval_xy -v
"""

from __future__ import annotations

import importlib
import io
import json
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

_action_eval = importlib.import_module("_action_eval")
evaluate_single_xy = _action_eval.evaluate_single_xy
evaluate_pairs = _action_eval.evaluate_pairs
parse_action = _action_eval.parse_action

# 테스트용 UI State. 루트 div 가 화면 전체를 덮으므로 화면 안 좌표는 항상 어떤
# element 에 속한다 — no-bbox 를 만들려면 화면 밖 좌표를 써야 한다.
UI_XML = """<div bounds="[0,0][840,1876]" point="[420,938]">
  <button bounds="[100,200][300,280]" point="[200,240]">OK</button>
  <button bounds="[400,200][700,280]" point="[550,240]">Cancel</button>
  <p bounds="[50,600][790,660]" point="[420,630]">Some label</p>
  <input bounds="[50,700][790,780]" point="[420,740]"/>
</div>"""


# cerebra(AC_EXP08) 스키마 fixture. 위치축이 data-bbox 이고 `point` 속성은 없다
# (EXP08 실데이터와 동일 — 좌표를 데이터에 박지 않는 것이 확정 제약이다).
# 루트 div 가 화면 전체를 덮는 구조는 UI_XML 과 같다.
CEREBRA_XML = """<div data-bbox="0 0 840 1876">
  <button aria-label="OK" data-bbox="100 200 300 280">OK</button>
  <button aria-label="Cancel" data-bbox="400 200 700 280">Cancel</button>
  <p data-bbox="50 600 790 660">Some label</p>
  <input placeholder="Search" data-bbox="50 700 790 780"/>
</div>"""


def _wrap(action):
    return f"<action>{json.dumps(action, separators=(',', ':'))}</action>"


def _gt_entry(action, ui_xml=UI_XML):
    """EXP05 test 샘플 형태: user content 에 UI State XML + [Screenshot] 마커."""
    return {
        "messages": [
            {"from": "system", "value": "sys"},
            {
                "from": "human",
                "value": f"Current UI State:\n{ui_xml}\n\n[Screenshot]\n<image>",
            },
            {"from": "gpt", "value": _wrap(action)},
        ]
    }


def _single(gt_action, pred_action, ui_xml=UI_XML, xml_schema="android"):
    entry = _gt_entry(gt_action, ui_xml)
    return evaluate_single_xy(
        parse_action(entry["messages"][-1]["value"]),
        parse_action(_wrap(pred_action)),
        _action_eval._extract_ui_xml(entry),
        xml_schema,
    )


class ClickBBox(unittest.TestCase):
    """click / long_press — GT 좌표가 속한 element 의 bbox 포함 판정."""

    def test_click_inside_bbox(self):
        # GT (200,240) → 최소 면적 element = button[100,200][300,280]
        # pred (290,275) 는 그 안 → 정답 (좌표가 정확히 같지 않아도 됨)
        r = _single(
            {"action": "click", "coordinate": [200, 240]},
            {"action": "click", "coordinate": [290, 275]},
        )
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_bbox_check"])
        self.assertFalse(r["no_bbox"])

    def test_click_boundary_inclusive(self):
        # bbox 경계 좌표 [100,200] 는 포함 (경계 포함 규칙)
        r = _single(
            {"action": "click", "coordinate": [200, 240]},
            {"action": "click", "coordinate": [100, 200]},
        )
        self.assertTrue(r["step_correct"])

    def test_click_outside_bbox(self):
        # pred 가 다른 버튼(Cancel) 위 → 오답
        r = _single(
            {"action": "click", "coordinate": [200, 240]},
            {"action": "click", "coordinate": [550, 240]},
        )
        self.assertFalse(r["step_correct"])
        self.assertFalse(r["no_bbox"])

    def test_click_smallest_area_element_wins(self):
        # GT 가 여러 element 에 포함되면 최소 면적이 GT bbox.
        # (200,240) 는 루트 div 와 button 양쪽에 속하지만 button 이 선택되므로
        # 루트 div 안이지만 button 밖인 좌표는 오답이어야 한다.
        r = _single(
            {"action": "click", "coordinate": [200, 240]},
            {"action": "click", "coordinate": [420, 1500]},
        )
        self.assertFalse(r["step_correct"])

    def test_click_no_bbox_counts_and_fails(self):
        # 화면 밖 GT 좌표 → 포함 element 없음 → 오답 + no_bbox 플래그
        r = _single(
            {"action": "click", "coordinate": [900, 2000]},
            {"action": "click", "coordinate": [900, 2000]},
        )
        self.assertFalse(r["step_correct"])
        self.assertTrue(r["no_bbox"])

    def test_long_press_inside_bbox(self):
        r = _single(
            {"action": "long_press", "coordinate": [550, 240]},
            {"action": "long_press", "coordinate": [500, 250]},
        )
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_bbox_check"])

    def test_click_pred_missing_coordinate(self):
        r = _single({"action": "click", "coordinate": [200, 240]}, {"action": "click"})
        self.assertFalse(r["step_correct"])


class SwipeDirection(unittest.TestCase):
    """swipe — start→end 벡터의 주 성분 방향만 매칭 (좌표값 자체는 무관)."""

    def test_swipe_up_match(self):
        r = _single(
            {"action": "swipe", "coordinate1": [420, 1400], "coordinate2": [420, 460]},
            {"action": "swipe", "coordinate1": [100, 900], "coordinate2": [100, 300]},
        )
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_dir_check"])

    def test_swipe_down_match(self):
        r = _single(
            {"action": "swipe", "coordinate1": [420, 460], "coordinate2": [420, 1400]},
            {"action": "swipe", "coordinate1": [420, 500], "coordinate2": [420, 900]},
        )
        self.assertTrue(r["step_correct"])

    def test_swipe_left_match(self):
        r = _single(
            {"action": "swipe", "coordinate1": [700, 900], "coordinate2": [100, 900]},
            {"action": "swipe", "coordinate1": [800, 300], "coordinate2": [200, 300]},
        )
        self.assertTrue(r["step_correct"])

    def test_swipe_right_match(self):
        r = _single(
            {"action": "swipe", "coordinate1": [100, 900], "coordinate2": [700, 900]},
            {"action": "swipe", "coordinate1": [50, 100], "coordinate2": [600, 100]},
        )
        self.assertTrue(r["step_correct"])

    def test_swipe_opposite_direction_fails(self):
        r = _single(
            {"action": "swipe", "coordinate1": [420, 1400], "coordinate2": [420, 460]},
            {"action": "swipe", "coordinate1": [420, 460], "coordinate2": [420, 1400]},
        )
        self.assertFalse(r["step_correct"])

    def test_swipe_diagonal_major_x(self):
        # dx=600, dy=300 → |dx| >= |dy| → right. pred 는 순수 right → 일치
        r = _single(
            {"action": "swipe", "coordinate1": [100, 100], "coordinate2": [700, 400]},
            {"action": "swipe", "coordinate1": [100, 900], "coordinate2": [700, 900]},
        )
        self.assertTrue(r["step_correct"])

    def test_swipe_diagonal_major_y(self):
        # dx=300, dy=800 → |dy| > |dx| → down. pred 는 right → 불일치
        r = _single(
            {"action": "swipe", "coordinate1": [100, 100], "coordinate2": [400, 900]},
            {"action": "swipe", "coordinate1": [100, 900], "coordinate2": [700, 900]},
        )
        self.assertFalse(r["step_correct"])

    def test_swipe_tie_prefers_horizontal(self):
        # |dx| == |dy| → 규칙상 left/right (현 구현 동작 고정)
        r = _single(
            {"action": "swipe", "coordinate1": [0, 0], "coordinate2": [500, 500]},
            {"action": "swipe", "coordinate1": [100, 900], "coordinate2": [700, 900]},
        )
        self.assertTrue(r["step_correct"])

    def test_swipe_zero_vector_fails(self):
        # start == end → 방향 없음 → 오답 (GT 방향이 None 이면 무조건 실패)
        r = _single(
            {"action": "swipe", "coordinate1": [420, 900], "coordinate2": [420, 900]},
            {"action": "swipe", "coordinate1": [420, 900], "coordinate2": [420, 900]},
        )
        self.assertFalse(r["step_correct"])


class CoordIrrelevantTypes(unittest.TestCase):
    """type / open / wait / navigate_* — 좌표 검사 없음."""

    def test_type_match_normalized(self):
        r = _single(
            {"action": "type", "text": "Hello World"},
            {"action": "type", "text": "hello world"},
        )
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_text_check"])
        self.assertFalse(r["has_bbox_check"])

    def test_type_mismatch(self):
        r = _single(
            {"action": "type", "text": "Hello"}, {"action": "type", "text": "Goodbye"}
        )
        self.assertFalse(r["step_correct"])

    def test_open_match_normalized(self):
        r = _single(
            {"action": "open", "app_name": "Plantum"},
            {"action": "open", "app_name": "plantum"},
        )
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_app_check"])

    def test_open_mismatch(self):
        r = _single(
            {"action": "open", "app_name": "Plantum"},
            {"action": "open", "app_name": "Xe"},
        )
        self.assertFalse(r["step_correct"])

    def test_wait_type_only(self):
        r = _single({"action": "wait"}, {"action": "wait"})
        self.assertTrue(r["step_correct"])

    def test_navigate_back_type_only(self):
        r = _single({"action": "navigate_back"}, {"action": "navigate_back"})
        self.assertTrue(r["step_correct"])

    def test_navigate_home_type_only(self):
        r = _single({"action": "navigate_home"}, {"action": "navigate_home"})
        self.assertTrue(r["step_correct"])

    def test_terminate_type_only(self):
        # EXP05/06 GT 는 종료 액션을 "terminate" 로 쓴다(구 스키마 "finish").
        # status/answer 필드는 대조하지 않고 type 일치만으로 정답이어야 한다.
        # 이 케이스가 누락돼 terminate(테스트셋의 18.6%)가 조용히 0점 처리됐었다.
        r = _single(
            {"action": "terminate", "status": "success", "answer": None},
            {"action": "terminate", "status": "infeasible", "answer": None},
        )
        self.assertTrue(r["step_correct"])

    def test_finish_type_only_still_supported(self):
        # 구 스키마 "finish" 도 계속 type-only 로 통과해야 한다(하위 호환).
        r = _single({"action": "finish"}, {"action": "finish"})
        self.assertTrue(r["step_correct"])

    def test_terminate_type_mismatch_fails(self):
        r = _single(
            {"action": "terminate", "status": "success", "answer": None},
            {"action": "click", "coordinate": [200, 240]},
        )
        self.assertFalse(r["step_correct"])

    def test_type_mismatch_blocks_field_check(self):
        # action type 불일치 → field_match 진입 전에 오답
        r = _single(
            {"action": "type", "text": "Hello"},
            {"action": "click", "coordinate": [200, 240]},
        )
        self.assertFalse(r["type_correct"])
        self.assertFalse(r["step_correct"])

    def test_pred_none(self):
        r = evaluate_single_xy(
            {"action": "click", "coordinate": [200, 240]}, None, UI_XML
        )
        self.assertFalse(r["parsed"])
        self.assertFalse(r["step_correct"])


class XyAggregate(unittest.TestCase):
    """evaluate_pairs(coord_mode='xy') 집계 — no_bbox_n / cond_* 키."""

    def _pairs(self, specs):
        gts = [_gt_entry(gt) for gt, _ in specs]
        preds = [{"predict": _wrap(pred)} for _, pred in specs]
        return gts, preds

    def test_no_bbox_counter_and_cond_keys(self):
        specs = [
            # click 3건: 내부(정답) / 외부(오답) / no-bbox(오답)
            (
                {"action": "click", "coordinate": [200, 240]},
                {"action": "click", "coordinate": [290, 275]},
            ),
            (
                {"action": "click", "coordinate": [200, 240]},
                {"action": "click", "coordinate": [550, 240]},
            ),
            (
                {"action": "click", "coordinate": [900, 2000]},
                {"action": "click", "coordinate": [900, 2000]},
            ),
            # swipe 1건 정답
            (
                {
                    "action": "swipe",
                    "coordinate1": [420, 1400],
                    "coordinate2": [420, 460],
                },
                {
                    "action": "swipe",
                    "coordinate1": [100, 900],
                    "coordinate2": [100, 300],
                },
            ),
        ]
        gts, preds = self._pairs(specs)
        m = evaluate_pairs(gts, preds, "xy")

        self.assertEqual(m["total"], 4)
        self.assertEqual(m["no_bbox_n"], 1)
        self.assertAlmostEqual(m["cond_bbox_acc"], 1 / 3, places=4)
        self.assertAlmostEqual(m["cond_dir_acc"], 1.0, places=4)
        self.assertAlmostEqual(m["step_accuracy"], 2 / 4, places=4)
        # xy 모드는 index 채점 키를 내지 않는다
        self.assertNotIn("cond_index_acc", m)

    def test_index_mode_unaffected(self):
        # 기본 모드(index)는 xy 키를 내지 않는다 — 하위호환 고정
        gts = [
            {
                "messages": [
                    {"from": "gpt", "value": '{"action_type":"click","index":"1"}'}
                ]
            }
        ]
        preds = [{"predict": '{"action_type":"click","index":"1"}'}]
        m = evaluate_pairs(gts, preds)
        self.assertIn("cond_index_acc", m)
        self.assertNotIn("cond_bbox_acc", m)
        self.assertNotIn("no_bbox_n", m)


class CoordSpaceWarning(unittest.TestCase):
    """pred 좌표계 sanity 경고 — 채점 결과는 바꾸지 않고 경고만."""

    def _run_capture(self, specs):
        gts = [_gt_entry(gt) for gt, _ in specs]
        preds = [{"predict": _wrap(pred)} for _, pred in specs]
        buf = io.StringIO()
        with redirect_stderr(buf):
            m = evaluate_pairs(gts, preds, "xy")
        return m, buf.getvalue()

    def test_normalized_coords_warn(self):
        # pred 가 0~1 정규화 좌표 → 경고. 채점은 그대로 오답(=0%) 유지.
        specs = [
            (
                {"action": "click", "coordinate": [200, 240]},
                {"action": "click", "coordinate": [0.24, 0.13]},
            ),
            (
                {"action": "click", "coordinate": [550, 240]},
                {"action": "click", "coordinate": [0.65, 0.13]},
            ),
        ]
        m, err = self._run_capture(specs)
        self.assertIn("정규화 좌표", err)
        self.assertAlmostEqual(m["step_accuracy"], 0.0, places=4)

    def test_out_of_range_coords_warn(self):
        specs = [
            (
                {"action": "click", "coordinate": [200, 240]},
                {"action": "click", "coordinate": [5000, 9000]},
            ),
            (
                {"action": "click", "coordinate": [550, 240]},
                {"action": "click", "coordinate": [4000, 8000]},
            ),
        ]
        _m, err = self._run_capture(specs)
        self.assertIn("화면 범위", err)

    def test_normal_pixel_coords_no_warning(self):
        specs = [
            (
                {"action": "click", "coordinate": [200, 240]},
                {"action": "click", "coordinate": [290, 275]},
            ),
            (
                {"action": "click", "coordinate": [550, 240]},
                {"action": "click", "coordinate": [500, 250]},
            ),
        ]
        m, err = self._run_capture(specs)
        self.assertNotIn("[warn]", err)
        self.assertAlmostEqual(m["step_accuracy"], 1.0, places=4)


class CerebraSchema(unittest.TestCase):
    """--xml-schema cerebra (AC_EXP08) — 위치축이 data-bbox 인 XML 의 bbox 채점.

    스키마는 **opt-in** 이다: 기본값 android 는 EXP01~07 채점을 바이트 단위로 보존하고,
    cerebra 를 명시해야 EXP08 을 제대로 읽는다. 두 방향을 모두 못박는다 — 어느 쪽이든
    어긋나면 에러가 아니라 지표가 조용히 무너지는 실패다.
    """

    def test_data_bbox_parsed_with_cerebra_schema(self):
        # GT (200,240) → 최소 면적 element = button data-bbox="100 200 300 280"
        r = _single(
            {"action": "click", "coordinate": [200, 240]},
            {"action": "click", "coordinate": [290, 275]},
            CEREBRA_XML,
            "cerebra",
        )
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_bbox_check"])
        self.assertFalse(r["no_bbox"])

    def test_default_schema_misses_cerebra_xml(self):
        # 기본값(android)은 data-bbox 를 읽지 않는다 → element 목록이 통째로 비어
        # 정답 좌표인데도 no_bbox 오답이 된다. 이것이 하드 제약 15f 의 실패 모습이다.
        r = _single(
            {"action": "click", "coordinate": [200, 240]},
            {"action": "click", "coordinate": [290, 275]},
            CEREBRA_XML,
        )
        self.assertFalse(r["step_correct"])
        self.assertTrue(r["no_bbox"])

    def test_cerebra_schema_on_android_xml_is_no_bbox(self):
        # 역방향 — 기존 실험군에 cerebra 를 주면 bounds 를 못 읽는다. opt-in 규약이
        # 대칭이라는 근거이자 "붙이면 안 되는 곳" 의 회귀 가드.
        r = _single(
            {"action": "click", "coordinate": [200, 240]},
            {"action": "click", "coordinate": [290, 275]},
            UI_XML,
            "cerebra",
        )
        self.assertFalse(r["step_correct"])
        self.assertTrue(r["no_bbox"])

    def test_cerebra_boundary_inclusive(self):
        # android 경로와 같은 경계 포함(<=) 규칙. 좌상단·우하단 꼭짓점 둘 다 정답.
        for pred_pt in ([100, 200], [300, 280]):
            with self.subTest(pred=pred_pt):
                r = _single(
                    {"action": "click", "coordinate": [200, 240]},
                    {"action": "click", "coordinate": pred_pt},
                    CEREBRA_XML,
                    "cerebra",
                )
                self.assertTrue(r["step_correct"])

    def test_cerebra_just_outside_boundary_fails(self):
        r = _single(
            {"action": "click", "coordinate": [200, 240]},
            {"action": "click", "coordinate": [301, 280]},
            CEREBRA_XML,
            "cerebra",
        )
        self.assertFalse(r["step_correct"])
        self.assertFalse(r["no_bbox"])

    def test_cerebra_smallest_area_element_wins(self):
        # 루트 div 안이지만 button 밖 → 오답 (android 경로와 같은 최소 면적 규칙)
        r = _single(
            {"action": "click", "coordinate": [200, 240]},
            {"action": "click", "coordinate": [420, 1500]},
            CEREBRA_XML,
            "cerebra",
        )
        self.assertFalse(r["step_correct"])

    def test_cerebra_off_screen_gt_is_no_bbox(self):
        r = _single(
            {"action": "click", "coordinate": [900, 2000]},
            {"action": "click", "coordinate": [900, 2000]},
            CEREBRA_XML,
            "cerebra",
        )
        self.assertFalse(r["step_correct"])
        self.assertTrue(r["no_bbox"])

    def test_malformed_data_bbox_dropped(self):
        # 토큰 수가 4가 아니거나 정수가 아닌 element 는 버린다 (음수는 허용).
        xml = (
            '<div data-bbox="0 0 840 1876">'
            '<button data-bbox="100 200 300">short</button>'
            '<button data-bbox="a b c d">nan</button>'
            '<button data-bbox="-10 -20 40 60">neg</button>'
            "</div>"
        )
        boxes = _action_eval._bbox_elements(xml, "cerebra")
        self.assertEqual(sorted(boxes), [(-10, -20, 40, 60), (0, 0, 840, 1876)])

    def test_evaluate_pairs_threads_schema(self):
        specs = [
            (
                {"action": "click", "coordinate": [200, 240]},
                {"action": "click", "coordinate": [290, 275]},
            ),
            (
                {"action": "click", "coordinate": [550, 240]},
                {"action": "click", "coordinate": [500, 250]},
            ),
        ]
        gts = [_gt_entry(gt, CEREBRA_XML) for gt, _ in specs]
        preds = [{"predict": _wrap(pred)} for _, pred in specs]

        m_cerebra = evaluate_pairs(gts, preds, "xy", "cerebra")
        self.assertAlmostEqual(m_cerebra["cond_bbox_acc"], 1.0, places=4)
        self.assertEqual(m_cerebra["no_bbox_n"], 0)

        m_default = evaluate_pairs(gts, preds, "xy")
        self.assertAlmostEqual(m_default["cond_bbox_acc"], 0.0, places=4)
        self.assertEqual(m_default["no_bbox_n"], 2)


class XmlSchemaStamp(unittest.TestCase):
    """xy 산출물의 `xml_schema` 스탬프 — 어떤 스키마로 채점됐는지 파일이 스스로 말한다.

    스탬프가 없으면 스키마 오지정이 `cond_bbox_acc=0` / `no_bbox_n` 급증이라는
    **간접 증상**으로만 드러난다. `_hungarian_eval` 의 동명 스탬프와 필드명·값 어휘가
    같아야 두 산출물을 같은 방식으로 감사할 수 있다.
    """

    def _pairs(self, ui_xml):
        gt = {"action": "click", "coordinate": [200, 240]}
        pred = {"action": "click", "coordinate": [290, 275]}
        return [_gt_entry(gt, ui_xml)], [{"predict": _wrap(pred)}]

    def test_default_stamps_android(self):
        gts, preds = self._pairs(UI_XML)
        self.assertEqual(evaluate_pairs(gts, preds, "xy")["xml_schema"], "android")

    def test_cerebra_stamps_cerebra(self):
        gts, preds = self._pairs(CEREBRA_XML)
        m = evaluate_pairs(gts, preds, "xy", "cerebra")
        self.assertEqual(m["xml_schema"], "cerebra")

    def test_index_mode_has_no_stamp(self):
        # index 모드는 bbox 파싱을 하지 않는다 → 스탬프가 무의미하고, 찍으면
        # EXP01~04 산출물의 바이트 불변이 깨진다.
        gts = [
            {
                "messages": [
                    {"from": "gpt", "value": '{"action_type":"click","index":"1"}'}
                ]
            }
        ]
        preds = [{"predict": '{"action_type":"click","index":"1"}'}]
        self.assertNotIn("xml_schema", evaluate_pairs(gts, preds))
        self.assertNotIn("xml_schema", evaluate_pairs(gts, preds, "index", "cerebra"))


class XmlSchemaFlag(unittest.TestCase):
    """`--xml-schema` 기본값·검증 — `_hungarian_eval` 의 동명 플래그와 같은 규약."""

    def test_default_is_android_without_env(self):
        with unittest.mock.patch.dict(_action_eval.os.environ, {}, clear=True):
            self.assertEqual(_action_eval._default_xml_schema(), "android")

    def test_env_var_overrides_default(self):
        with unittest.mock.patch.dict(
            _action_eval.os.environ, {"XML_SCHEMA": "cerebra"}
        ):
            self.assertEqual(_action_eval._default_xml_schema(), "cerebra")

    def test_explicit_flag_beats_env(self):
        # 명시 플래그 > 환경변수. CLI 를 실제로 통과시켜야 argparse 의 default 평가
        # 시점(파서 생성 시)까지 함께 고정된다.
        self.assertEqual(self._run_cli(env="cerebra", flag="android"), "android")

    def test_env_var_drives_cli_default(self):
        # 플래그를 안 주면 환경변수가 기본값이 된다 (셸 경유 재현 경로).
        self.assertEqual(self._run_cli(env="cerebra", flag=None), "cerebra")

    def _run_cli(self, env, flag):
        """score 서브커맨드를 실제로 돌려 산출물의 `xml_schema` 스탬프를 돌려준다."""
        gt = {"action": "click", "coordinate": [200, 240]}
        pred = {"action": "click", "coordinate": [290, 275]}
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "test.jsonl").write_text(
                json.dumps(_gt_entry(gt, CEREBRA_XML), ensure_ascii=False) + "\n"
            )
            (d / "pred.jsonl").write_text(
                json.dumps({"predict": _wrap(pred)}, ensure_ascii=False) + "\n"
            )
            argv = [
                "_action_eval.py",
                "score",
                "--test",
                str(d / "test.jsonl"),
                "--pred",
                str(d / "pred.jsonl"),
                "--coord-mode",
                "xy",
                "--output",
                str(d / "m.json"),
            ]
            if flag:
                argv += ["--xml-schema", flag]
            with (
                unittest.mock.patch.dict(_action_eval.os.environ, {"XML_SCHEMA": env}),
                unittest.mock.patch.object(sys, "argv", argv),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(_action_eval.main(), 0)
            return json.loads((d / "m.json").read_text())["xml_schema"]

    def test_check_rejects_unknown_schema(self):
        # 환경변수로 들어온 값은 argparse choices 를 통과하지 않으므로 검증이 필요하다.
        with self.assertRaises(ValueError):
            _action_eval.check_xml_schema("bounds")
        self.assertEqual(_action_eval.check_xml_schema("cerebra"), "cerebra")


if __name__ == "__main__":
    unittest.main()
