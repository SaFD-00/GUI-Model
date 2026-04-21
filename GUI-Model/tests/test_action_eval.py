"""Regression tests for scripts/_action_eval.py Step Accuracy metric.

Run:
    cd GUI-Model
    python -m unittest tests.test_action_eval -v
    # or with pytest if available:
    pytest tests/test_action_eval.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import importlib

_action_eval = importlib.import_module("_action_eval")
evaluate_single = _action_eval.evaluate_single
evaluate_predictions = _action_eval.evaluate_predictions
evaluate_pairs = _action_eval.evaluate_pairs
parse_action = _action_eval.parse_action


def _gt_pred_jsonl(pairs):
    """Build temp gt/pred jsonl files from list of (gt_action, pred_text) tuples."""
    gt_f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    pr_f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for gt, pred_text in pairs:
        gt_f.write(json.dumps({"messages": [{"from": "gpt", "value": json.dumps(gt)}]}) + "\n")
        pr_f.write(json.dumps({"predict": pred_text}) + "\n")
    gt_f.close()
    pr_f.close()
    return Path(gt_f.name), Path(pr_f.name)


class StepAccuracySingle(unittest.TestCase):
    """Per-type field_match rules for evaluate_single."""

    # ── click ────────────────────────────────────────────────────────────
    def test_click_correct(self):
        r = evaluate_single({"type": "click", "index": "12"},
                            {"type": "click", "index": "12"})
        self.assertTrue(r["step_correct"])

    def test_click_wrong_index(self):
        r = evaluate_single({"type": "click", "index": "12"},
                            {"type": "click", "index": "9"})
        self.assertFalse(r["step_correct"])

    def test_click_int_vs_str(self):
        # str(12) == str("12") — robustness
        r = evaluate_single({"type": "click", "index": "12"},
                            {"type": "click", "index": 12})
        self.assertTrue(r["step_correct"])

    # ── long_click ───────────────────────────────────────────────────────
    def test_long_click_correct(self):
        r = evaluate_single({"type": "long_click", "index": "5"},
                            {"type": "long_click", "index": "5"})
        self.assertTrue(r["step_correct"])

    def test_long_click_wrong(self):
        r = evaluate_single({"type": "long_click", "index": "5"},
                            {"type": "long_click", "index": "6"})
        self.assertFalse(r["step_correct"])

    # ── scroll ───────────────────────────────────────────────────────────
    def test_scroll_correct(self):
        r = evaluate_single({"type": "scroll", "direction": "down"},
                            {"type": "scroll", "direction": "down"})
        self.assertTrue(r["step_correct"])

    def test_scroll_wrong_direction(self):
        r = evaluate_single({"type": "scroll", "direction": "down"},
                            {"type": "scroll", "direction": "up"})
        self.assertFalse(r["step_correct"])

    def test_scroll_normalization(self):
        r = evaluate_single({"type": "scroll", "direction": "down"},
                            {"type": "scroll", "direction": " DOWN "})
        self.assertTrue(r["step_correct"])

    # ── open_app (nested params) ─────────────────────────────────────────
    def test_open_app_correct_nested(self):
        r = evaluate_single({"type": "open_app", "params": {"app": "Gmail"}},
                            {"type": "open_app", "params": {"app": "Gmail"}})
        self.assertTrue(r["step_correct"])

    def test_open_app_wrong_app(self):
        r = evaluate_single({"type": "open_app", "params": {"app": "Gmail"}},
                            {"type": "open_app", "params": {"app": "Calendar"}})
        self.assertFalse(r["step_correct"])

    def test_open_app_top_level_app_fallback(self):
        # pred 가 nested 가 아닌 top-level 로 출력해도 인식 (관용적 처리)
        r = evaluate_single({"type": "open_app", "params": {"app": "Gmail"}},
                            {"type": "open_app", "app": "gmail"})
        self.assertTrue(r["step_correct"])

    # ── input (nested params, gt.index=null 무시) ────────────────────────
    def test_input_correct_nested(self):
        r = evaluate_single({"type": "input", "index": None, "params": {"text": "hello"}},
                            {"type": "input", "index": None, "params": {"text": "hello"}})
        self.assertTrue(r["step_correct"])

    def test_input_text_mismatch(self):
        r = evaluate_single({"type": "input", "index": None, "params": {"text": "hello"}},
                            {"type": "input", "index": None, "params": {"text": "world"}})
        self.assertFalse(r["step_correct"])

    def test_input_text_normalization(self):
        r = evaluate_single({"type": "input", "index": None, "params": {"text": "Hello"}},
                            {"type": "input", "index": None, "params": {"text": " hello "}})
        self.assertTrue(r["step_correct"])

    # ── navigate_back (type-only) ────────────────────────────────────────
    def test_navigate_back_correct(self):
        r = evaluate_single({"type": "navigate_back"},
                            {"type": "navigate_back"})
        self.assertTrue(r["step_correct"])

    def test_navigate_back_type_wrong(self):
        r = evaluate_single({"type": "navigate_back"},
                            {"type": "click", "index": "1"})
        self.assertFalse(r["step_correct"])

    # ── finish (type-only, status 단일값이라 검증 불요) ─────────────────
    def test_finish_correct(self):
        r = evaluate_single({"type": "finish", "status": "complete"},
                            {"type": "finish", "status": "complete"})
        self.assertTrue(r["step_correct"])

    def test_finish_status_irrelevant(self):
        # 데이터셋 status 는 단일값이라 type 일치만으로 정답
        r = evaluate_single({"type": "finish", "status": "complete"},
                            {"type": "finish"})
        self.assertTrue(r["step_correct"])

    # ── 공통: type 불일치 / pred=None / unknown ─────────────────────────
    def test_type_mismatch_zero(self):
        r = evaluate_single({"type": "click", "index": "1"},
                            {"type": "scroll", "direction": "down"})
        self.assertFalse(r["step_correct"])
        self.assertFalse(r["type_correct"])

    def test_pred_none(self):
        r = evaluate_single({"type": "click", "index": "1"}, None)
        self.assertFalse(r["step_correct"])

    def test_unknown_gt_type(self):
        # 코드 경로 robustness — 알 수 없는 type 은 type 일치만 본다
        r = evaluate_single({"type": "spell_cast", "magic": "fireball"},
                            {"type": "spell_cast", "magic": "fireball"})
        # type 일치는 True 지만 field_match 가 정의 안 됐으니 False 가 자연스러움
        # (구현 정책: unknown type 은 step_correct=False)
        self.assertFalse(r["step_correct"])
        self.assertTrue(r["type_correct"])

    # ── 추가 pin-down 케이스 ─────────────────────────────────────────────
    def test_click_both_index_none(self):
        # 양쪽 index=None → str(None)==str(None) 로 True (현 구현 동작 고정)
        r = evaluate_single({"type": "click", "index": None},
                            {"type": "click", "index": None})
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_index_check"])

    def test_scroll_both_direction_missing(self):
        # 양쪽 direction 부재 → _norm(None)=='' 동치 → True
        r = evaluate_single({"type": "scroll"}, {"type": "scroll"})
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_dir_check"])

    def test_open_app_empty_params_dict(self):
        # params={} 양쪽 → _pval 이 None 반환 → _norm 동치 True
        r = evaluate_single({"type": "open_app", "params": {}},
                            {"type": "open_app", "params": {}})
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_app_check"])

    def test_input_top_level_text_fallback(self):
        # gt 는 nested, pred 는 top-level text — _pval 이 top-level 우선
        r = evaluate_single({"type": "input", "index": None, "params": {"text": "hello"}},
                            {"type": "input", "text": "hello"})
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["has_text_check"])

    def test_type_case_insensitive(self):
        # 'click' vs 'CLICK' → lower() 정규화로 type_correct True
        r = evaluate_single({"type": "click", "index": "3"},
                            {"type": "CLICK", "index": "3"})
        self.assertTrue(r["step_correct"])
        self.assertTrue(r["type_correct"])

    def test_type_whitespace_not_stripped(self):
        # 현 구현은 type 에 strip 하지 않음 (lower 만) — 회귀 방지용 고정
        r = evaluate_single({"type": "click", "index": "3"},
                            {"type": " click ", "index": "3"})
        self.assertFalse(r["type_correct"])
        self.assertFalse(r["step_correct"])

    def test_unknown_type_has_checks_all_false(self):
        # unknown type 분기에서 has_*_check 가 어느 것도 켜지지 않아야 함
        r = evaluate_single({"type": "spell_cast"}, {"type": "spell_cast"})
        self.assertFalse(r["has_index_check"])
        self.assertFalse(r["has_dir_check"])
        self.assertFalse(r["has_app_check"])
        self.assertFalse(r["has_text_check"])

    def test_pred_none_all_flags_false(self):
        # pred_action=None → parsed 포함 모든 플래그 False
        r = evaluate_single({"type": "click", "index": "1"}, None)
        self.assertFalse(r["parsed"])
        self.assertFalse(r["type_correct"])
        self.assertFalse(r["step_correct"])
        self.assertFalse(r["has_index_check"])
        self.assertFalse(r["has_dir_check"])
        self.assertFalse(r["has_app_check"])
        self.assertFalse(r["has_text_check"])


class StepAccuracyAggregate(unittest.TestCase):
    """evaluate_predictions 집계 로직."""

    def test_micro_macro_aggregation(self):
        pairs = [
            # 5 click: 4 correct (80%)
            ({"type": "click", "index": "1"}, '{"type":"click","index":"1"}'),
            ({"type": "click", "index": "2"}, '{"type":"click","index":"2"}'),
            ({"type": "click", "index": "3"}, '{"type":"click","index":"3"}'),
            ({"type": "click", "index": "4"}, '{"type":"click","index":"4"}'),
            ({"type": "click", "index": "5"}, '{"type":"click","index":"X"}'),  # wrong
            # 2 scroll: 1 correct (50%)
            ({"type": "scroll", "direction": "down"}, '{"type":"scroll","direction":"down"}'),
            ({"type": "scroll", "direction": "up"},   '{"type":"scroll","direction":"down"}'),  # wrong
            # 1 navigate_back: 1 correct
            ({"type": "navigate_back"}, '{"type":"navigate_back"}'),
        ]
        gt_p, pr_p = _gt_pred_jsonl(pairs)
        try:
            m = evaluate_predictions(str(gt_p), str(pr_p))
        finally:
            gt_p.unlink(); pr_p.unlink()

        self.assertEqual(m["total"], 8)
        # micro SA = 6/8 = 0.75
        self.assertAlmostEqual(m["step_accuracy"], 6 / 8, places=4)
        # macro SA = mean(click=0.8, scroll=0.5, navigate_back=1.0) = 0.7666...
        self.assertAlmostEqual(m["macro_step_accuracy"], (0.8 + 0.5 + 1.0) / 3, places=4)
        # type_acc = 8/8 = 1.0 (모두 type 맞음)
        self.assertAlmostEqual(m["type_accuracy"], 1.0, places=4)
        # cond_index_acc: click 5건 중 정답 4
        self.assertAlmostEqual(m["cond_index_acc"], 4 / 5, places=4)
        # cond_dir_acc: scroll 2건 중 정답 1
        self.assertAlmostEqual(m["cond_dir_acc"], 1 / 2, places=4)
        # parse_rate = 1.0
        self.assertAlmostEqual(m["parse_rate"], 1.0, places=4)
        # per_type 키 존재
        for t in ("click", "scroll", "navigate_back"):
            self.assertIn(t, m["per_type"])
            self.assertIn("step_acc", m["per_type"][t])
            self.assertIn("count", m["per_type"][t])
        # bounds-related 키 부재
        self.assertNotIn("avg_bounds_iou", m)
        self.assertNotIn("cond_bounds_iou", m)

    def test_parse_failure_zero(self):
        pairs = [
            ({"type": "click", "index": "1"}, "this is not json"),
            ({"type": "click", "index": "2"}, '{"type":"click","index":"2"}'),
        ]
        gt_p, pr_p = _gt_pred_jsonl(pairs)
        try:
            m = evaluate_predictions(str(gt_p), str(pr_p))
        finally:
            gt_p.unlink(); pr_p.unlink()
        self.assertAlmostEqual(m["step_accuracy"], 0.5, places=4)
        self.assertAlmostEqual(m["parse_rate"], 0.5, places=4)

    def test_codefence_parsing(self):
        pairs = [
            ({"type": "click", "index": "1"},
             '```json\n{"type":"click","index":"1"}\n```'),
        ]
        gt_p, pr_p = _gt_pred_jsonl(pairs)
        try:
            m = evaluate_predictions(str(gt_p), str(pr_p))
        finally:
            gt_p.unlink(); pr_p.unlink()
        self.assertAlmostEqual(m["step_accuracy"], 1.0, places=4)
        self.assertAlmostEqual(m["parse_rate"], 1.0, places=4)

    def test_length_mismatch_warns(self):
        # gt 3, pred 2 → 짧은 쪽에 맞춰 자르되 metrics 는 계산되어야 함
        gt_f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        pr_f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for i in range(3):
            gt_f.write(json.dumps({"messages": [{"from": "gpt",
                                                 "value": '{"type":"click","index":"' + str(i) + '"}'}]}) + "\n")
        for i in range(2):
            pr_f.write(json.dumps({"predict": '{"type":"click","index":"' + str(i) + '"}'}) + "\n")
        gt_f.close(); pr_f.close()
        try:
            m = evaluate_predictions(gt_f.name, pr_f.name)
        finally:
            Path(gt_f.name).unlink(); Path(pr_f.name).unlink()
        # 2 건만 채점되어야 함
        self.assertEqual(m["total"], 2)

    # ── 추가 pin-down 케이스 ─────────────────────────────────────────────
    def test_unknown_type_lowers_macro(self):
        # click 2건 (정답) + unknown 1건 → macro = (1.0 + 0.0) / 2 = 0.5
        pairs = [
            ({"type": "click", "index": "1"}, '{"type":"click","index":"1"}'),
            ({"type": "click", "index": "2"}, '{"type":"click","index":"2"}'),
            ({"type": "spell_cast", "magic": "fire"},
             '{"type":"spell_cast","magic":"fire"}'),
        ]
        gt_p, pr_p = _gt_pred_jsonl(pairs)
        try:
            m = evaluate_predictions(str(gt_p), str(pr_p))
        finally:
            gt_p.unlink(); pr_p.unlink()
        self.assertEqual(m["total"], 3)
        self.assertAlmostEqual(m["type_accuracy"], 1.0, places=4)
        # micro SA = 2/3
        self.assertAlmostEqual(m["step_accuracy"], 2 / 3, places=4)
        # macro = (click_step_acc + spell_cast_step_acc) / 2 = (1.0 + 0.0) / 2
        self.assertAlmostEqual(m["macro_step_accuracy"], 0.5, places=4)
        self.assertIn("spell_cast", m["per_type"])
        self.assertEqual(m["per_type"]["spell_cast"]["count"], 1)
        self.assertEqual(m["per_type"]["spell_cast"]["step_acc"], 0.0)

    def test_cond_acc_zero_when_no_type(self):
        # navigate_back 1건만 → 모든 cond_*_acc 는 n=0 로 0.0
        pairs = [
            ({"type": "navigate_back"}, '{"type":"navigate_back"}'),
        ]
        gt_p, pr_p = _gt_pred_jsonl(pairs)
        try:
            m = evaluate_predictions(str(gt_p), str(pr_p))
        finally:
            gt_p.unlink(); pr_p.unlink()
        self.assertAlmostEqual(m["cond_index_acc"], 0.0, places=4)
        self.assertAlmostEqual(m["cond_dir_acc"],   0.0, places=4)
        self.assertAlmostEqual(m["cond_app_acc"],   0.0, places=4)
        self.assertAlmostEqual(m["cond_text_acc"],  0.0, places=4)

    def test_output_key_fallback(self):
        # pred_entry 에 predict 대신 output 키 — line 180 fallback 고정
        gt_f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        pr_f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        gt_f.write(json.dumps({"messages": [{"from": "gpt",
                                             "value": '{"type":"click","index":"5"}'}]}) + "\n")
        pr_f.write(json.dumps({"output": '{"type":"click","index":"5"}'}) + "\n")
        gt_f.close(); pr_f.close()
        try:
            m = evaluate_predictions(gt_f.name, pr_f.name)
        finally:
            Path(gt_f.name).unlink(); Path(pr_f.name).unlink()
        self.assertAlmostEqual(m["step_accuracy"], 1.0, places=4)
        self.assertAlmostEqual(m["parse_rate"],    1.0, places=4)

    def test_per_type_count_sum_equals_total(self):
        # per_type 전체 count 합 == total 불변식
        pairs = [
            ({"type": "click", "index": "1"}, '{"type":"click","index":"1"}'),
            ({"type": "click", "index": "2"}, '{"type":"click","index":"2"}'),
            ({"type": "click", "index": "3"}, '{"type":"click","index":"X"}'),
            ({"type": "scroll", "direction": "down"},
             '{"type":"scroll","direction":"down"}'),
            ({"type": "scroll", "direction": "up"},
             '{"type":"scroll","direction":"up"}'),
            ({"type": "finish", "status": "complete"}, '{"type":"finish"}'),
        ]
        gt_p, pr_p = _gt_pred_jsonl(pairs)
        try:
            m = evaluate_predictions(str(gt_p), str(pr_p))
        finally:
            gt_p.unlink(); pr_p.unlink()
        self.assertEqual(
            sum(v["count"] for v in m["per_type"].values()),
            m["total"],
        )

    def test_finish_status_different_value_still_correct(self):
        # finish 는 type-only 정책 → status 가 달라도 step_correct True
        pairs = [
            ({"type": "finish", "status": "complete"},
             '{"type":"finish","status":"failed"}'),
        ]
        gt_p, pr_p = _gt_pred_jsonl(pairs)
        try:
            m = evaluate_predictions(str(gt_p), str(pr_p))
        finally:
            gt_p.unlink(); pr_p.unlink()
        self.assertAlmostEqual(m["step_accuracy"], 1.0, places=4)
        self.assertAlmostEqual(m["type_accuracy"], 1.0, places=4)


class ParseAction(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(parse_action('{"type":"click","index":"1"}'),
                         {"type": "click", "index": "1"})

    def test_codefence_json(self):
        self.assertEqual(parse_action('```json\n{"type":"click","index":"2"}\n```'),
                         {"type": "click", "index": "2"})

    def test_codefence_no_lang(self):
        self.assertEqual(parse_action('```\n{"type":"click","index":"3"}\n```'),
                         {"type": "click", "index": "3"})

    def test_garbage(self):
        self.assertIsNone(parse_action("hello world"))

    def test_empty(self):
        self.assertIsNone(parse_action(""))

    def test_inline_extraction_from_garbage(self):
        # 앞뒤 garbage 사이에 단순 객체 — 최종 fallback regex (\{[^{}]*\}) 분기
        self.assertEqual(
            parse_action('blah blah {"type":"click","index":"1"} trailing'),
            {"type": "click", "index": "1"},
        )

    def test_nested_json_via_json_loads(self):
        # 중첩 object 는 첫 json.loads 분기로 통과 (inline regex 는 nested 처리 불가)
        self.assertEqual(
            parse_action('{"type":"open_app","params":{"app":"Gmail"}}'),
            {"type": "open_app", "params": {"app": "Gmail"}},
        )

    def test_codefence_multiline_whitespace(self):
        # 코드펜스 앞뒤 다중 개행 + fence 내부 공백 라인 포함
        self.assertEqual(
            parse_action('\n\n```json\n\n{"type":"click","index":"9"}\n\n```\n'),
            {"type": "click", "index": "9"},
        )


class IdOodAggregation(unittest.TestCase):
    """evaluate_pairs 로 ID + OOD 통합 집계가 올바른지 검증."""

    def _mk_pairs(self, specs):
        gt_entries, pred_entries = [], []
        for gt, pred_text in specs:
            gt_entries.append({"messages": [{"from": "gpt", "value": json.dumps(gt)}]})
            pred_entries.append({"predict": pred_text})
        return gt_entries, pred_entries

    def test_overall_equals_concat_of_id_and_ood(self):
        id_specs = [
            ({"type": "click", "index": "3"}, '{"type":"click","index":"3"}'),     # correct
            ({"type": "click", "index": "4"}, '{"type":"click","index":"9"}'),     # wrong index
        ]
        ood_specs = [
            ({"type": "scroll", "direction": "down"}, '{"type":"scroll","direction":"up"}'),   # wrong dir
            ({"type": "navigate_back"}, '{"type":"navigate_back"}'),                            # correct
        ]
        gt_id, pr_id = self._mk_pairs(id_specs)
        gt_ood, pr_ood = self._mk_pairs(ood_specs)

        m_id = evaluate_pairs(gt_id, pr_id)
        m_ood = evaluate_pairs(gt_ood, pr_ood)
        m_all = evaluate_pairs(gt_id + gt_ood, pr_id + pr_ood)

        self.assertEqual(m_id["total"], 2)
        self.assertEqual(m_ood["total"], 2)
        self.assertEqual(m_all["total"], 4)
        # overall step_accuracy = (1 + 1) / 4 = 0.5
        self.assertAlmostEqual(m_all["step_accuracy"], 0.5, places=4)
        # in_domain: 1 of 2 correct → 0.5; out_of_domain: 1 of 2 → 0.5
        self.assertAlmostEqual(m_id["step_accuracy"], 0.5, places=4)
        self.assertAlmostEqual(m_ood["step_accuracy"], 0.5, places=4)

    def test_per_type_counts_merge_across_splits(self):
        id_specs = [
            ({"type": "click", "index": "1"}, '{"type":"click","index":"1"}'),
            ({"type": "click", "index": "2"}, '{"type":"click","index":"2"}'),
        ]
        ood_specs = [
            ({"type": "click", "index": "9"}, '{"type":"click","index":"0"}'),
        ]
        gt_id, pr_id = self._mk_pairs(id_specs)
        gt_ood, pr_ood = self._mk_pairs(ood_specs)
        m_all = evaluate_pairs(gt_id + gt_ood, pr_id + pr_ood)
        self.assertEqual(m_all["per_type"]["click"]["count"], 3)
        # 2 correct out of 3
        self.assertAlmostEqual(m_all["per_type"]["click"]["step_acc"], 2 / 3, places=4)


if __name__ == "__main__":
    unittest.main()
