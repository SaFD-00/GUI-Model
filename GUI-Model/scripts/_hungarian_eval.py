#!/usr/bin/env python3
"""
Standalone Hungarian/BLEU/ROUGE evaluator for Stage 1 World-Modeling predictions.

Ported from gui-model.ipynb Cell 55+56. Used by scripts/stage1_eval.sh
and can be re-run from notebook Cell 56 with identical results.

Subcommand
----------
score   : 단일 prediction.jsonl 의 평균 메트릭 계산 → hungarian_metrics.json 저장

Example
-------
  python scripts/_hungarian_eval.py score \\
      --test  data/AndroidControl/gui-model_stage1_test.jsonl \\
      --pred  outputs/AC/eval/{MODEL}/stage1_eval/full_world_model/epoch-1/generated_predictions.jsonl \\
      --output outputs/AC/eval/{MODEL}/stage1_eval/full_world_model/epoch-1/hungarian_metrics.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

# bs4 / munkres 는 score 서브커맨드에서만 사용. 지연 로딩.
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
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
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


def main():
    parser = argparse.ArgumentParser(description="Stage 1 Hungarian/BLEU/ROUGE evaluator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_score = sub.add_parser("score", help="Compute metrics for a single prediction jsonl")
    p_score.add_argument("--test", required=True, help="Ground-truth test jsonl (with messages/images)")
    p_score.add_argument("--pred", required=True, help="Model prediction jsonl (generated_predictions.jsonl)")
    p_score.add_argument("--output", required=True, help="Output metrics.json path")
    p_score.set_defaults(func=_cmd_score)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
