#!/usr/bin/env python3
"""
Standalone Stage 2 Action Prediction evaluator + winner selector.

본 스크립트의 정본은 ``gui-model.ipynb`` Cell 139 이며 두 곳의 채점 함수는
글자 단위로 동일하게 유지된다. 메트릭은 AndroidControl 데이터셋의 실제 스키마
(``bounds`` 필드 영구 부재, element-index 기반 grounding) 에 맞춘
**Step Accuracy (SA)** 단일 1차 지표를 사용한다.

Subcommands
-----------
score   : 단일 prediction.jsonl 에 대한 Action 메트릭 → action_metrics.json 저장
select  : lora 변형별 여러 checkpoint 결과를 비교해 winner 를 BEST_CHECKPOINT 파일에 기록

Examples
--------
  python scripts/_action_eval.py score \\
      --test   data/AndroidControl/gui-model_stage2_test.jsonl \\
      --pred   outputs/AC/eval/{MODEL}/stage2_eval/lora_world_model/checkpoint-1360/generated_predictions.jsonl \\
      --output outputs/AC/eval/{MODEL}/stage2_eval/lora_world_model/checkpoint-1360/action_metrics.json

  python scripts/_action_eval.py select \\
      --eval-dir  outputs/AC/eval/{MODEL}/stage2_eval/lora_world_model \\
      --train-dir outputs/AC/adapters/{MODEL}_stage2_lora_world_model \\
      --metric    step_accuracy

Step Accuracy 정의 (요약)
-------------------------
SA = (1/N) · Σ correct_i,  correct_i = 1 iff (parse_ok ∧ type==gt ∧ field_match(type))

  type            field_match
  ─────────────── ──────────────────────────────────────────────
  navigate_back   (필드 없음) → 항상 통과
  finish          (status 단일값) → 항상 통과
  click           str(pred.index) == str(gt.index)
  long_click      str(pred.index) == str(gt.index)
  scroll          norm(direction) 일치
  open_app        norm(params.app) 일치
  input           norm(params.text) 일치 (gt.index=null 무시)

  norm(s) = str(s or '').strip().lower()

Reference baselines (보고용)
  - type random baseline: 1/7 ≈ 14.3%
  - scroll majority baseline (down): 79.0%
  - finish.status constant baseline: 100% (해석 무의미)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


# ── Action Parsing ───────────────────────────────────────────────────────
def parse_action(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    match = re.search(r'\{[^{}]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


# ── Field 추출 헬퍼 (top-level + nested params 모두 지원) ────────────────
def _pval(action, key):
    if action is None:
        return None
    if key in action:
        return action[key]
    return (action.get('params') or {}).get(key)


def _norm(s):
    return str(s if s is not None else '').strip().lower()


# ── Step Accuracy 채점 ───────────────────────────────────────────────────
# field_match(type) 가 정의된 type 만 step_correct 로 인정. 그 외 type 은 False.
_FIELD_MATCH_TYPES = {
    'navigate_back', 'finish', 'click', 'long_click',
    'scroll', 'open_app', 'input',
}


def evaluate_single(gt_action, pred_action):
    result = {
        'parsed': pred_action is not None,
        'type_correct': False,
        'step_correct': False,
        'has_index_check': False,    # click / long_click
        'has_dir_check': False,      # scroll
        'has_app_check': False,      # open_app
        'has_text_check': False,     # input
    }
    if pred_action is None:
        return result

    gt_type = str(gt_action.get('type', '')).lower()
    pred_type = str(pred_action.get('type', '')).lower()
    result['type_correct'] = (gt_type == pred_type)
    if not result['type_correct']:
        return result

    # field_match
    if gt_type in ('navigate_back', 'finish'):
        result['step_correct'] = True
        return result

    if gt_type in ('click', 'long_click'):
        result['has_index_check'] = True
        result['step_correct'] = (
            str(gt_action.get('index')) == str(pred_action.get('index'))
        )
        return result

    if gt_type == 'scroll':
        result['has_dir_check'] = True
        result['step_correct'] = (
            _norm(_pval(gt_action, 'direction')) == _norm(_pval(pred_action, 'direction'))
        )
        return result

    if gt_type == 'open_app':
        result['has_app_check'] = True
        result['step_correct'] = (
            _norm(_pval(gt_action, 'app')) == _norm(_pval(pred_action, 'app'))
        )
        return result

    if gt_type == 'input':
        result['has_text_check'] = True
        result['step_correct'] = (
            _norm(_pval(gt_action, 'text')) == _norm(_pval(pred_action, 'text'))
        )
        return result

    # unknown type — type 만 일치해도 step 은 False (정책)
    return result


def evaluate_predictions(test_path, pred_path):
    with open(test_path, 'r') as f:
        gt_entries = [json.loads(line) for line in f if line.strip()]
    with open(pred_path, 'r') as f:
        pred_entries = [json.loads(line) for line in f if line.strip()]

    if len(gt_entries) != len(pred_entries):
        print(f"[warn] length mismatch: gt={len(gt_entries)} pred={len(pred_entries)} "
              f"→ truncating to {min(len(gt_entries), len(pred_entries))}", file=sys.stderr)

    results = []
    per_type = defaultdict(lambda: {
        'count': 0, 'type_correct': 0, 'step_correct': 0,
    })
    cond = {
        'index': {'n': 0, 'k': 0},   # click + long_click
        'dir':   {'n': 0, 'k': 0},   # scroll
        'app':   {'n': 0, 'k': 0},   # open_app
        'text':  {'n': 0, 'k': 0},   # input
    }

    for gt_entry, pred_entry in zip(gt_entries, pred_entries):
        gt_action = json.loads(gt_entry['messages'][-1]['value'])
        pred_text = pred_entry.get('predict', pred_entry.get('output', ''))
        pred_action = parse_action(pred_text)

        r = evaluate_single(gt_action, pred_action)
        gt_type = str(gt_action.get('type', 'unknown')).lower()
        r['gt_type'] = gt_type
        results.append(r)

        per_type[gt_type]['count'] += 1
        per_type[gt_type]['type_correct'] += int(r['type_correct'])
        per_type[gt_type]['step_correct'] += int(r['step_correct'])

        if r['has_index_check']:
            cond['index']['n'] += 1
            cond['index']['k'] += int(r['step_correct'])
        if r['has_dir_check']:
            cond['dir']['n'] += 1
            cond['dir']['k'] += int(r['step_correct'])
        if r['has_app_check']:
            cond['app']['n'] += 1
            cond['app']['k'] += int(r['step_correct'])
        if r['has_text_check']:
            cond['text']['n'] += 1
            cond['text']['k'] += int(r['step_correct'])

    total = len(results)
    parsed = sum(1 for r in results if r['parsed'])
    type_correct = sum(int(r['type_correct']) for r in results)
    step_correct = sum(int(r['step_correct']) for r in results)

    parse_rate = parsed / total if total else 0
    type_acc = type_correct / total if total else 0
    step_acc = step_correct / total if total else 0

    per_type_summary = {}
    for t, d in per_type.items():
        per_type_summary[t] = {
            'count':    d['count'],
            'type_acc': round(d['type_correct'] / d['count'] if d['count'] else 0, 4),
            'step_acc': round(d['step_correct'] / d['count'] if d['count'] else 0, 4),
        }

    macro_step = (sum(v['step_acc'] for v in per_type_summary.values()) /
                  len(per_type_summary)) if per_type_summary else 0

    def _ratio(c):
        return c['k'] / c['n'] if c['n'] else 0

    return {
        'total':                total,
        'parse_rate':           round(parse_rate, 4),
        'type_accuracy':        round(type_acc, 4),
        'step_accuracy':        round(step_acc, 4),
        'macro_step_accuracy':  round(macro_step, 4),
        'cond_index_acc':       round(_ratio(cond['index']), 4),
        'cond_dir_acc':         round(_ratio(cond['dir']),   4),
        'cond_app_acc':         round(_ratio(cond['app']),   4),
        'cond_text_acc':        round(_ratio(cond['text']),  4),
        'per_type':             per_type_summary,
    }


# ── CLI ──────────────────────────────────────────────────────────────────
def _cmd_score(args):
    metrics = evaluate_predictions(args.test, args.pred)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"[score] pred={args.pred}")
    print(f"[score] total={metrics['total']}  parse={metrics['parse_rate']:.2%}  "
          f"type={metrics['type_accuracy']:.4f}  step={metrics['step_accuracy']:.4f}  "
          f"macro={metrics['macro_step_accuracy']:.4f}  "
          f"index={metrics['cond_index_acc']:.4f}  dir={metrics['cond_dir_acc']:.4f}  "
          f"app={metrics['cond_app_acc']:.4f}  text={metrics['cond_text_acc']:.4f}")
    print(f"[score] saved: {args.output}")
    return 0


def _ckpt_step(name: str) -> int:
    try:
        return int(name.split("-", 1)[1])
    except (IndexError, ValueError):
        return -1


_SELECT_KEYS = (
    "step_accuracy", "macro_step_accuracy", "type_accuracy", "parse_rate",
    "cond_index_acc", "cond_dir_acc", "cond_app_acc", "cond_text_acc",
)


def _cmd_select(args):
    eval_dir  = Path(args.eval_dir)
    train_dir = Path(args.train_dir)
    metric    = args.metric
    hf_repo_template = getattr(args, "hf_repo_template", None)

    # epoch-*/ 우선, 하위호환으로 checkpoint-*/ 지원.
    matches = sorted(eval_dir.glob("epoch-*/action_metrics.json"),
                     key=lambda p: _ckpt_step(p.parent.name))
    if not matches:
        matches = sorted(eval_dir.glob("checkpoint-*/action_metrics.json"),
                         key=lambda p: _ckpt_step(p.parent.name))

    candidates = []
    for mpath in matches:
        try:
            with open(mpath, 'r', encoding='utf-8') as f:
                m = json.load(f)
        except Exception as e:
            print(f"[select] WARN: skip {mpath} ({e})", file=sys.stderr)
            continue
        name = mpath.parent.name
        epoch = _ckpt_step(name) if name.startswith("epoch-") else None
        row = {
            "checkpoint":   name,
            "step":         _ckpt_step(name),
            "epoch":        epoch,
            "metrics_path": str(mpath),
        }
        for k in _SELECT_KEYS:
            row[k] = m.get(k, 0.0)
        row["total"] = m.get("total", 0)
        candidates.append(row)

    if not candidates:
        print(f"[select] ERROR: no metrics under {eval_dir}/{{epoch,checkpoint}}-*/",
              file=sys.stderr)
        return 2

    # winner: metric 최고값, 동률 시 step 큰 쪽
    winner = max(candidates, key=lambda c: (c[metric], c["step"]))

    # 요약 출력
    print(f"[select] metric = {metric}  (tie-breaker: larger step)")
    print(f"[select] eval_dir={eval_dir}")
    header = (f"{'checkpoint':<20} {'step':>7} {'step_acc':>9} {'macro':>7} "
              f"{'type':>7} {'index':>7} {'dir':>7} {'app':>7} {'text':>7} {'parse':>7}")
    print(header)
    print("-" * len(header))
    for c in candidates:
        mark = "  <-- winner" if c is winner else ""
        print(f"{c['checkpoint']:<20} {c['step']:>7} "
              f"{c['step_accuracy']:>9.4f} {c['macro_step_accuracy']:>7.4f} "
              f"{c['type_accuracy']:>7.4f} {c['cond_index_acc']:>7.4f} "
              f"{c['cond_dir_acc']:>7.4f} {c['cond_app_acc']:>7.4f} "
              f"{c['cond_text_acc']:>7.4f} {c['parse_rate']:>7.2%}{mark}")

    # --hf-repo-template 로 winner/candidate hf_repo_id 주입
    if hf_repo_template:
        for c in candidates:
            if c["epoch"] is not None:
                c["hf_repo_id"] = hf_repo_template.replace("{epoch}", str(c["epoch"]))
        winner_repo_id = (hf_repo_template.replace("{epoch}", str(winner["epoch"]))
                          if winner["epoch"] is not None else None)
    else:
        winner_repo_id = None

    train_dir.mkdir(parents=True, exist_ok=True)
    (train_dir / "BEST_CHECKPOINT").write_text(winner["checkpoint"] + "\n", encoding='utf-8')
    summary = {
        "selected":     winner["checkpoint"],
        "epoch":        winner["epoch"],
        "hf_repo_id":   winner_repo_id,
        "metric":       metric,
        "metric_value": winner[metric],
        "candidates":   candidates,
    }
    (train_dir / "BEST_CHECKPOINT.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    print(f"[select] winner = {winner['checkpoint']}  ({metric}={winner[metric]:.4f})")
    if winner_repo_id:
        print(f"[select] hf_repo_id = {winner_repo_id}")
    print(f"[select] wrote: {train_dir / 'BEST_CHECKPOINT'}")
    print(f"[select] wrote: {train_dir / 'BEST_CHECKPOINT.json'}")
    return 0


def main():
    p = argparse.ArgumentParser(description="Stage 2 Action Prediction evaluator + winner selector")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_s = sub.add_parser("score", help="Compute action metrics for a single prediction jsonl")
    p_s.add_argument("--test", required=True)
    p_s.add_argument("--pred", required=True)
    p_s.add_argument("--output", required=True)
    p_s.set_defaults(func=_cmd_score)

    p_l = sub.add_parser("select", help="Select winner checkpoint by metric")
    p_l.add_argument("--eval-dir", required=True,
                     help="Directory containing epoch-*/ or checkpoint-*/ action_metrics.json (variant-specific)")
    p_l.add_argument("--train-dir", required=True,
                     help="Training output_dir where BEST_CHECKPOINT will be written")
    p_l.add_argument("--metric", default="step_accuracy",
                     help=f"Metric key to maximize (default: step_accuracy). "
                          f"Options: {', '.join(_SELECT_KEYS)}")
    p_l.add_argument("--hf-repo-template", default=None, dest="hf_repo_template",
                     help="Optional HF repo id template with '{epoch}' placeholder. "
                          "When provided, winner/candidate hf_repo_id is written to BEST_CHECKPOINT.json.")
    p_l.set_defaults(func=_cmd_select)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
