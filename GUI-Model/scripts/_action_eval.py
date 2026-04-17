#!/usr/bin/env python3
"""
Standalone Stage 2 Action Prediction evaluator + winner selector.

Ported from gui-model.ipynb Cell 41+42. 구조는 scripts/_hungarian_eval.py 와 동일하게
`score` / `select` 두 서브커맨드를 제공하여 shell 파이프라인과 notebook 이 동일 결과를 낸다.

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
      --train-dir outputs/AC/adapters/{MODEL}/stage2_lora_world_model \\
      --metric    overall_score
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


# ── Action Parsing & IoU (Cell 41 복제) ──────────────────────────────────
def parse_bounds(bounds_str):
    try:
        nums = re.findall(r'[\d.]+', str(bounds_str))
        return [float(n) for n in nums[:4]] if len(nums) >= 4 else None
    except Exception:
        return None


def calc_iou(box1, box2):
    if box1 is None or box2 is None:
        return 0.0
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = max(0, box1[2] - box1[0]) * max(0, box1[3] - box1[1])
    area2 = max(0, box2[2] - box2[0]) * max(0, box2[3] - box2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0.0


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


def evaluate_single(gt_action, pred_action):
    result = {'type_correct': False, 'iou': 0.0, 'params_correct': False,
              'has_bounds': False, 'has_params': False}
    if pred_action is None:
        return result
    gt_type = gt_action.get('type', '')
    pred_type = pred_action.get('type', '')
    result['type_correct'] = (gt_type.lower() == pred_type.lower())

    gt_bounds = parse_bounds(gt_action.get('bounds'))
    pred_bounds = parse_bounds(pred_action.get('bounds'))
    if gt_bounds is not None:
        result['has_bounds'] = True
        result['iou'] = calc_iou(gt_bounds, pred_bounds)

    param_keys = {'openapp': 'app', 'input': 'text', 'swipe': 'direction'}
    if gt_type.lower() in param_keys:
        result['has_params'] = True
        key = param_keys[gt_type.lower()]
        gt_val = str(gt_action.get(key, '')).strip().lower()
        pred_val = str(pred_action.get(key, '')).strip().lower()
        result['params_correct'] = (gt_val == pred_val)
    return result


def evaluate_predictions(test_path, pred_path):
    with open(test_path, 'r') as f:
        gt_entries = [json.loads(line) for line in f if line.strip()]
    with open(pred_path, 'r') as f:
        pred_entries = [json.loads(line) for line in f if line.strip()]

    results = []
    per_type = defaultdict(lambda: {'count': 0, 'type_correct': 0,
                                    'iou_sum': 0.0, 'iou_count': 0,
                                    'params_correct': 0, 'params_count': 0})

    for gt_entry, pred_entry in zip(gt_entries, pred_entries):
        gt_action = json.loads(gt_entry['messages'][-1]['value'])
        pred_text = pred_entry.get('predict', pred_entry.get('output', ''))
        pred_action = parse_action(pred_text)

        r = evaluate_single(gt_action, pred_action)
        r['gt_type'] = gt_action.get('type', 'unknown').lower()
        r['parsed'] = (pred_action is not None)
        results.append(r)

        t = r['gt_type']
        per_type[t]['count'] += 1
        per_type[t]['type_correct'] += int(r['type_correct'])
        if r['has_bounds']:
            per_type[t]['iou_sum'] += r['iou']
            per_type[t]['iou_count'] += 1
        if r['has_params']:
            per_type[t]['params_correct'] += int(r['params_correct'])
            per_type[t]['params_count'] += 1

    total = len(results)
    type_correct = sum(r['type_correct'] for r in results)
    bounds_entries = [r for r in results if r['has_bounds']]
    params_entries = [r for r in results if r['has_params']]

    type_acc = type_correct / total if total else 0
    avg_iou = sum(r['iou'] for r in results) / total if total else 0
    cond_iou = sum(r['iou'] for r in bounds_entries) / len(bounds_entries) if bounds_entries else 0
    params_acc_all = sum(r['params_correct'] for r in results) / total if total else 0
    cond_params = sum(r['params_correct'] for r in params_entries) / len(params_entries) if params_entries else 0
    overall = type_acc * (0.5 * avg_iou + 0.5 * params_acc_all)
    parse_rate = sum(1 for r in results if r['parsed']) / total if total else 0

    per_type_summary = {}
    for t, d in per_type.items():
        per_type_summary[t] = {
            'count': d['count'],
            'type_acc': round(d['type_correct'] / d['count'] if d['count'] else 0, 4),
            'avg_iou': round(d['iou_sum'] / d['iou_count'] if d['iou_count'] else 0, 4),
            'params_acc': round(d['params_correct'] / d['params_count'] if d['params_count'] else 0, 4),
        }

    return {
        'total': total,
        'parse_rate':      round(parse_rate, 4),
        'type_accuracy':   round(type_acc, 4),
        'avg_bounds_iou':  round(avg_iou, 4),
        'cond_bounds_iou': round(cond_iou, 4),
        'params_accuracy': round(params_acc_all, 4),
        'cond_params_acc': round(cond_params, 4),
        'overall_score':   round(overall, 4),
        'per_type':        per_type_summary,
    }


# ── CLI ──────────────────────────────────────────────────────────────────
def _cmd_score(args):
    metrics = evaluate_predictions(args.test, args.pred)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"[score] pred={args.pred}")
    print(f"[score] total={metrics['total']}  parse={metrics['parse_rate']:.2%}  "
          f"type={metrics['type_accuracy']:.4f}  iou={metrics['avg_bounds_iou']:.4f}  "
          f"params={metrics['params_accuracy']:.4f}  overall={metrics['overall_score']:.4f}")
    print(f"[score] saved: {args.output}")
    return 0


def _ckpt_step(name: str) -> int:
    try:
        return int(name.split("-", 1)[1])
    except (IndexError, ValueError):
        return -1


def _cmd_select(args):
    eval_dir  = Path(args.eval_dir)
    train_dir = Path(args.train_dir)
    metric    = args.metric

    candidates = []
    for mpath in sorted(eval_dir.glob("checkpoint-*/action_metrics.json"),
                        key=lambda p: _ckpt_step(p.parent.name)):
        try:
            with open(mpath, 'r', encoding='utf-8') as f:
                m = json.load(f)
        except Exception as e:
            print(f"[select] WARN: skip {mpath} ({e})", file=sys.stderr)
            continue
        candidates.append({
            "checkpoint": mpath.parent.name,
            "step": _ckpt_step(mpath.parent.name),
            "metrics_path": str(mpath),
            metric: m.get(metric, 0.0),
            "parse_rate": m.get("parse_rate", 0.0),
            "type_accuracy": m.get("type_accuracy", 0.0),
            "avg_bounds_iou": m.get("avg_bounds_iou", 0.0),
            "params_accuracy": m.get("params_accuracy", 0.0),
            "overall_score": m.get("overall_score", 0.0),
            "total": m.get("total", 0),
        })

    if not candidates:
        print(f"[select] ERROR: no checkpoint metrics under {eval_dir}/checkpoint-*/",
              file=sys.stderr)
        return 2

    # winner: metric 최고값, 동률 시 step 큰 쪽
    winner = max(candidates, key=lambda c: (c[metric], c["step"]))

    # 요약 출력
    print(f"[select] metric = {metric}  (tie-breaker: larger step)")
    print(f"[select] eval_dir={eval_dir}")
    header = f"{'checkpoint':<20} {'step':>7} {metric:>15} {'type_acc':>9} {'iou':>7} {'params':>7} {'overall':>9} {'parse':>7}"
    print(header)
    print("-" * len(header))
    for c in candidates:
        mark = "  <-- winner" if c is winner else ""
        print(f"{c['checkpoint']:<20} {c['step']:>7} {c[metric]:>15.4f} "
              f"{c['type_accuracy']:>9.4f} {c['avg_bounds_iou']:>7.4f} "
              f"{c['params_accuracy']:>7.4f} {c['overall_score']:>9.4f} "
              f"{c['parse_rate']:>7.2%}{mark}")

    train_dir.mkdir(parents=True, exist_ok=True)
    (train_dir / "BEST_CHECKPOINT").write_text(winner["checkpoint"] + "\n", encoding='utf-8')
    summary = {
        "selected": winner["checkpoint"],
        "metric": metric,
        "metric_value": winner[metric],
        "candidates": candidates,
    }
    (train_dir / "BEST_CHECKPOINT.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    print(f"[select] winner = {winner['checkpoint']}  ({metric}={winner[metric]:.4f})")
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
                     help="Directory containing checkpoint-*/action_metrics.json (variant-specific)")
    p_l.add_argument("--train-dir", required=True,
                     help="Training output_dir where BEST_CHECKPOINT will be written")
    p_l.add_argument("--metric", default="overall_score",
                     help="Metric key to maximize (default: overall_score). "
                          "Options: overall_score, type_accuracy, avg_bounds_iou, params_accuracy, parse_rate")
    p_l.set_defaults(func=_cmd_select)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
