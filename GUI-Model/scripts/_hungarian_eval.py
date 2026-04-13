#!/usr/bin/env python3
"""
Standalone Hungarian/BLEU/ROUGE evaluator for Stage 1 World-Modeling predictions.

Ported from gui-model.ipynb Cell 25+26. Used by scripts/stage1_eval.sh
and can be re-run from notebook Cell 26 with identical results.

Subcommands
-----------
score   : 단일 prediction.jsonl 의 평균 메트릭 계산 → hungarian_metrics.json 저장
select  : 여러 checkpoint 평가 결과를 비교해 winner 를 BEST_CHECKPOINT 파일에 기록

Examples
--------
  python scripts/_hungarian_eval.py score \\
      --test  data/AndroidControl/gui-model_stage1_test.jsonl \\
      --pred  saves/AC/stage1_eval/hungarian_matching/checkpoint-1055/generated_predictions.jsonl \\
      --output saves/AC/stage1_eval/hungarian_matching/checkpoint-1055/hungarian_metrics.json

  python scripts/_hungarian_eval.py select \\
      --eval-dir  saves/AC/stage1_eval/hungarian_matching \\
      --train-dir saves/AC/stage1_full/full_world_model \\
      --metric    avg_hungarian_f1
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

# bs4 / munkres 는 score 서브커맨드에서만 사용. select 는 미설치 환경에서도 돌아야 하므로
# 지연 로딩 (매크로 import 대신 _lazy_deps() 호출).
BeautifulSoup = None  # type: ignore
Munkres = None  # type: ignore

def _lazy_deps():
    """bs4 / munkres 를 지연 로드. score 서브커맨드 진입 시 한 번 호출."""
    global BeautifulSoup, Munkres
    if BeautifulSoup is None:
        from bs4 import BeautifulSoup as _BS
        BeautifulSoup = _BS
    if Munkres is None:
        from munkres import Munkres as _M
        Munkres = _M


# ── Hungarian Metric 상수 (Cell 25 상수 복제) ──────────────────────────────
INTERACTIVE_TAGS = {"button", "input", "a", "select", "textarea"}
CONTENT_TAGS     = {"p", "img", "span"}
CLICKABLE_ATTRS  = {"clickable", "long-clickable"}

W_TAG   = 3.0
W_TEXT  = 1.5
W_INDEX = 0.2

MATCH_THRESHOLD = 1.5
INDEX_TAU       = 2


# ── 요소 추출 ────────────────────────────────────────────────────────────
def _collect_texts(el):
    tokens = set()
    def add(v):
        if v:
            tokens.add(v.strip())
    add(el.get("description"))
    add(el.get("id"))
    for child in el.find_all(True):
        add(child.get("description"))
        add(child.get("id"))
        t = child.get_text(strip=True)
        if t:
            tokens.add(t)
    t = el.get_text(strip=True)
    if t:
        tokens.add(t)
    return " | ".join(sorted(tokens)) if tokens else ""


def _safe_int(v, default=-1):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def extract_elements(xml_str):
    try:
        soup = BeautifulSoup(xml_str, "xml")
    except Exception:
        soup = BeautifulSoup(xml_str, "html.parser")
    elements = []
    for el in soup.find_all(True):
        tag  = el.name
        idx  = _safe_int(el.get("index", -1))
        text = _collect_texts(el)
        is_interactive = tag in INTERACTIVE_TAGS
        is_content     = (tag in CONTENT_TAGS) and bool(text)
        is_clickable   = any(el.get(a) for a in CLICKABLE_ATTRS)
        if is_interactive or is_content or is_clickable:
            elements.append({"tag": tag, "text": text, "index": idx})
    return elements


# ── 매칭 비용 & Hungarian ───────────────────────────────────────────────
def _text_sim(a, b):
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    sa = set(a.lower().replace("|", "").split())
    sb = set(b.lower().replace("|", "").split())
    return len(sa & sb) / len(sa | sb)


def _match_cost(e1, e2, max_idx):
    if e1["tag"] != e2["tag"]:
        return W_TAG
    tc = W_TEXT  * (1.0 - _text_sim(e1["text"], e2["text"]))
    ic = W_INDEX * (abs(e1["index"] - e2["index"]) / max(max_idx, 1))
    return round(tc + ic, 5)


def _hungarian_match(pred, gt):
    n, m = len(pred), len(gt)
    if n == 0 or m == 0:
        return [], []
    max_idx = max(
        (e["index"] for e in pred + gt if e["index"] >= 0),
        default=1,
    )
    matrix = [[_match_cost(p, g, max_idx) for g in gt] for p in pred]
    size = max(n, m)
    padded = [row + [MATCH_THRESHOLD * 2] * (size - len(row)) for row in matrix]
    while len(padded) < size:
        padded.append([MATCH_THRESHOLD * 2] * size)
    indexes = Munkres().compute(padded)
    pairs = []
    for i, j in indexes:
        if i < n and j < m and matrix[i][j] < MATCH_THRESHOLD:
            pairs.append((i, j, matrix[i][j]))
    return pairs, matrix


def compute_hungarian_acc(pred_str, gt_str):
    _zero = {
        "hungarian_ea": 0.0, "hungarian_f1": 0.0,
        "hungarian_prec": 0.0, "hungarian_rec": 0.0,
        "hungarian_text": 0.0, "hungarian_idx": 0.0,
    }
    try:
        pred_els = extract_elements(pred_str)
        gt_els   = extract_elements(gt_str)
    except Exception:
        return _zero
    if not gt_els:
        return _zero

    pairs, _ = _hungarian_match(pred_els, gt_els)
    n_pred, n_gt, n_matched = len(pred_els), len(gt_els), len(pairs)

    ea   = n_matched / max(n_pred, n_gt) if max(n_pred, n_gt) > 0 else 0.0
    prec = n_matched / n_pred             if n_pred  > 0           else 0.0
    rec  = n_matched / n_gt               if n_gt    > 0           else 0.0
    f1   = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0    else 0.0

    if pairs:
        text_sims = [_text_sim(pred_els[i]["text"], gt_els[j]["text"]) for i, j, _ in pairs]
        idx_diffs = [abs(pred_els[i]["index"] - gt_els[j]["index"]) for i, j, _ in pairs]
        text_avg  = sum(text_sims) / len(text_sims)
        idx_acc   = sum(1 for d in idx_diffs if d <= INDEX_TAU) / len(idx_diffs)
    else:
        text_avg = 0.0
        idx_acc  = 0.0

    return {
        "hungarian_ea":   round(ea, 4),
        "hungarian_f1":   round(f1, 4),
        "hungarian_prec": round(prec, 4),
        "hungarian_rec":  round(rec, 4),
        "hungarian_text": round(text_avg, 4),
        "hungarian_idx":  round(idx_acc, 4),
    }


# ── BLEU / ROUGE-L ──────────────────────────────────────────────────────
def calc_bleu(reference, hypothesis, max_n=4):
    ref_tokens = reference.split()
    hyp_tokens = hypothesis.split()
    if not hyp_tokens or not ref_tokens:
        return 0.0
    bp = min(1.0, math.exp(1 - len(ref_tokens) / len(hyp_tokens)))
    precisions = []
    for n in range(1, max_n + 1):
        ref_ngrams = Counter(tuple(ref_tokens[i:i+n]) for i in range(len(ref_tokens) - n + 1))
        hyp_ngrams = Counter(tuple(hyp_tokens[i:i+n]) for i in range(len(hyp_tokens) - n + 1))
        clipped = sum(min(count, ref_ngrams.get(ng, 0)) for ng, count in hyp_ngrams.items())
        total = sum(hyp_ngrams.values())
        precisions.append(0 if total == 0 else clipped / total)
    if any(p == 0 for p in precisions):
        return 0.0
    log_avg = sum(math.log(p) for p in precisions) / max_n
    return bp * math.exp(log_avg)


def calc_rouge_l(reference, hypothesis):
    ref_tokens = reference.split()
    hyp_tokens = hypothesis.split()
    if not ref_tokens or not hyp_tokens:
        return 0.0
    m, n = len(ref_tokens), len(hyp_tokens)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref_tokens[i-1] == hyp_tokens[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
    lcs_len = dp[m][n]
    precision = lcs_len / n
    recall    = lcs_len / m
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# ── 전체 평가 (Cell 26 evaluate_stage1_predictions 포팅) ───────────────
def evaluate_stage1_predictions(test_path, pred_path):
    with open(test_path, 'r') as f:
        gt_entries = [json.loads(line) for line in f if line.strip()]
    with open(pred_path, 'r') as f:
        pred_entries = [json.loads(line) for line in f if line.strip()]

    results = []
    for gt_entry, pred_entry in zip(gt_entries, pred_entries):
        gt_text = gt_entry['messages'][-1]['value']
        pred_text = pred_entry.get('predict', pred_entry.get('output', ''))
        results.append({
            'bleu':        calc_bleu(gt_text, pred_text),
            'rouge_l':     calc_rouge_l(gt_text, pred_text),
            'exact_match': 1.0 if gt_text.strip() == pred_text.strip() else 0.0,
            'hungarian':   compute_hungarian_acc(pred_text, gt_text),
        })

    total = len(results)
    avg = lambda key: sum(r[key] for r in results) / total if total else 0.0
    hung_avg = lambda key: sum(r['hungarian'][key] for r in results) / total if total else 0.0
    return {
        'total': total,
        'avg_bleu':           round(avg('bleu'), 4),
        'avg_rouge_l':        round(avg('rouge_l'), 4),
        'exact_match_rate':   round(avg('exact_match'), 4),
        'avg_hungarian_ea':   round(hung_avg('hungarian_ea'), 4),
        'avg_hungarian_f1':   round(hung_avg('hungarian_f1'), 4),
        'avg_hungarian_prec': round(hung_avg('hungarian_prec'), 4),
        'avg_hungarian_rec':  round(hung_avg('hungarian_rec'), 4),
        'avg_hungarian_text': round(hung_avg('hungarian_text'), 4),
        'avg_hungarian_idx':  round(hung_avg('hungarian_idx'), 4),
    }


# ── CLI ──────────────────────────────────────────────────────────────────
def _cmd_score(args):
    _lazy_deps()
    metrics = evaluate_stage1_predictions(args.test, args.pred)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"[score] pred={args.pred}")
    print(f"[score] total={metrics['total']}  f1={metrics['avg_hungarian_f1']:.4f}  "
          f"bleu={metrics['avg_bleu']:.4f}  rouge-l={metrics['avg_rouge_l']:.4f}")
    print(f"[score] saved: {args.output}")
    return 0


def _ckpt_step(name: str) -> int:
    """'checkpoint-1234' → 1234. 그 외 → -1."""
    try:
        return int(name.split("-", 1)[1])
    except (IndexError, ValueError):
        return -1


def _cmd_select(args):
    eval_dir  = Path(args.eval_dir)
    train_dir = Path(args.train_dir)
    metric    = args.metric

    # checkpoint-*/hungarian_metrics.json 수집
    candidates = []
    for mpath in sorted(eval_dir.glob("checkpoint-*/hungarian_metrics.json"),
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
            "avg_bleu": m.get("avg_bleu", 0.0),
            "avg_rouge_l": m.get("avg_rouge_l", 0.0),
            "avg_hungarian_f1": m.get("avg_hungarian_f1", 0.0),
            "avg_hungarian_ea": m.get("avg_hungarian_ea", 0.0),
            "exact_match_rate": m.get("exact_match_rate", 0.0),
            "total": m.get("total", 0),
        })

    if not candidates:
        print(f"[select] ERROR: no checkpoint metrics found under {eval_dir}/checkpoint-*/",
              file=sys.stderr)
        return 2

    # Optional: Baseline (비교용) 로드
    baseline_path = eval_dir / "base" / "hungarian_metrics.json"
    baseline = None
    if baseline_path.exists():
        try:
            with open(baseline_path, 'r', encoding='utf-8') as f:
                baseline = json.load(f)
        except Exception:
            baseline = None

    # Optional: trainer_state.json 에서 epoch 별 eval_loss 추출 (교차확인용)
    tstate_path = train_dir / "trainer_state.json"
    eval_loss_by_step = {}
    if tstate_path.exists():
        try:
            with open(tstate_path, 'r', encoding='utf-8') as f:
                tstate = json.load(f)
            for ev in tstate.get("log_history", []):
                if "eval_loss" in ev and "step" in ev:
                    eval_loss_by_step[int(ev["step"])] = float(ev["eval_loss"])
        except Exception:
            pass
    for c in candidates:
        c["eval_loss"] = eval_loss_by_step.get(c["step"])

    # winner 선정: metric 최고값, 동률 시 step 큰 쪽
    winner = max(candidates, key=lambda c: (c[metric], c["step"]))

    # 요약 테이블 출력
    print(f"[select] metric = {metric}  (tie-breaker: larger step)")
    print(f"[select] eval_dir={eval_dir}")
    header = f"{'checkpoint':<20} {'step':>7} {metric:>20} {'avg_bleu':>10} {'rouge_l':>10} {'EM%':>6} {'eval_loss':>10}"
    print(header)
    print("-" * len(header))
    if baseline is not None:
        print(f"{'baseline (0-shot)':<20} {'-':>7} {baseline.get(metric, 0.0):>20.4f} "
              f"{baseline.get('avg_bleu', 0.0):>10.4f} {baseline.get('avg_rouge_l', 0.0):>10.4f} "
              f"{baseline.get('exact_match_rate', 0.0)*100:>6.2f} {'-':>10}")
    for c in candidates:
        mark = "  <-- winner" if c is winner else ""
        el_str = f"{c['eval_loss']:.4f}" if c["eval_loss"] is not None else "-"
        print(f"{c['checkpoint']:<20} {c['step']:>7} {c[metric]:>20.4f} "
              f"{c['avg_bleu']:>10.4f} {c['avg_rouge_l']:>10.4f} "
              f"{c['exact_match_rate']*100:>6.2f} {el_str:>10}{mark}")

    # BEST_CHECKPOINT 기록
    train_dir.mkdir(parents=True, exist_ok=True)
    (train_dir / "BEST_CHECKPOINT").write_text(winner["checkpoint"] + "\n", encoding='utf-8')
    summary = {
        "selected": winner["checkpoint"],
        "metric": metric,
        "metric_value": winner[metric],
        "baseline": baseline,
        "candidates": candidates,
    }
    (train_dir / "BEST_CHECKPOINT.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    print(f"[select] winner = {winner['checkpoint']}  ({metric}={winner[metric]:.4f})")
    print(f"[select] wrote: {train_dir / 'BEST_CHECKPOINT'}")
    print(f"[select] wrote: {train_dir / 'BEST_CHECKPOINT.json'}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Stage 1 Hungarian/BLEU/ROUGE evaluator + winner selector")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_score = sub.add_parser("score", help="Compute metrics for a single prediction jsonl")
    p_score.add_argument("--test", required=True, help="Ground-truth test jsonl (with messages/images)")
    p_score.add_argument("--pred", required=True, help="Model prediction jsonl (generated_predictions.jsonl)")
    p_score.add_argument("--output", required=True, help="Output metrics.json path")
    p_score.set_defaults(func=_cmd_score)

    p_sel = sub.add_parser("select", help="Select winner checkpoint by metric")
    p_sel.add_argument("--eval-dir", required=True,
                       help="Directory containing checkpoint-*/hungarian_metrics.json (and optional base/)")
    p_sel.add_argument("--train-dir", required=True,
                       help="Training output_dir where BEST_CHECKPOINT will be written")
    p_sel.add_argument("--metric", default="avg_hungarian_f1",
                       help="Metric key to maximize (default: avg_hungarian_f1)")
    p_sel.set_defaults(func=_cmd_select)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
