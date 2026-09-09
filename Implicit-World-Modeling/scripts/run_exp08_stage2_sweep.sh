#!/usr/bin/env bash
# AC_EXP08 stage2 eval v2 스윕 — 구 매트릭스 전량을 GPU 여러 장에 나눠 돌린다.
#
# 왜 이 스크립트가 필요한가
# -------------------------
# `stage2_eval.sh` 는 (variant, stage1-variant, stage1-epoch, epochs) 한 조합을 순차로 돈다.
# 구 매트릭스는 그런 조합이 34 개이고 각 조합이 5 버킷을 낳는다 (총 170 leaf). GPU 4 장에
# 나누려면 조합을 쪼개 배분해야 하는데, leaf 마다 `skip_if_done` marker 가 독립이라
# **같은 GPU 에 다시 걸어도 끝난 leaf 는 다시 안 돈다** — 중단·재개가 안전하다.
#
# 배분 단위는 "invocation 1 개 = epoch 1 개 = 5 leaf" 다. 라운드로빈으로 GPU 에 흩는다.
#
# 재고 현실 (2026-09-08 HF 조회로 확인)
# -------------------------------------
#   - `lora_world-model_from_full-ep3/epoch-0.75` 는 **HF 에 없다** (401). 구 표에는 있는데
#     로컬 merged 도 사라져 재현 불가 → 이 스윕에서 빠진다. 34/35 유닛만 돈다.
#   - `lora_base_lr-1e-04/epoch-1` 은 HF 에 있으나(`base-lr-1e-04-stage2-lora-epoch1`)
#     셸 CLI 가 그 repo id 를 조립할 수 없다 (`STAGE2_ALL_VARIANTS` 에 lr 축이 없다).
#     → `--include-lr-variant` 를 주면 같은 3 커맨드를 직접 조립해 돈다 (leaf 이름·채점 플래그 동일).
#
# 사용법
# ------
#   bash run_exp08_stage2_sweep.sh --repo /path/to/Implicit-World-Modeling --gpus 0,1,2,3 --dry-run
#   bash run_exp08_stage2_sweep.sh --repo /path/to/Implicit-World-Modeling --gpus 0,1,2,3
#   bash run_exp08_stage2_sweep.sh --repo ... --gpus 0,1,2,3 --include-lr-variant
#
# 필수 환경: conda env(기본 implicit-world-modeling) · LlamaFactory 체크아웃 · HF 접근 토큰
set -euo pipefail

REPO=""
GPUS="0,1,2,3"
CONDA_ENV="${CONDA_ENV:-/opt/miniconda3/envs/implicit-world-modeling}"
DRY_RUN=0
INCLUDE_LR=0
LOGDIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --gpus) GPUS="$2"; shift 2 ;;
    --conda-env) CONDA_ENV="$2"; shift 2 ;;
    --logdir) LOGDIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --include-lr-variant) INCLUDE_LR=1; shift ;;
    -h|--help) sed -n '1,32p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$REPO" ]] || { echo "--repo 는 필수" >&2; exit 2; }
REPO="$(cd "$REPO" && pwd)"
[[ -f "$REPO/scripts/stage2_eval.sh" ]] || { echo "stage2_eval.sh 가 없다: $REPO" >&2; exit 2; }
LOGDIR="${LOGDIR:-$REPO/logs/sweep_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$LOGDIR"

IFS=',' read -r -a GPU_ARR <<< "$GPUS"
NGPU="${#GPU_ARR[@]}"

# ── 작업 목록 ────────────────────────────────────────────────────────────────
# 한 줄 = stage2_eval.sh 인자 (한 epoch = 5 leaf). 순서는 아카이브 매트릭스 그대로.
TASKS=()
add() { TASKS+=("$*"); }

add "--variants base --epochs 1 --stage1-epoch 3"
for e in 0.25 0.5 0.76 1; do add "--variants full_base --epochs $e --stage1-epoch 3"; done
for e in 1 2 3;           do add "--variants lora_base --epochs $e --stage1-epoch 3"; done
for e in 0.25 0.5 0.75 1; do add "--variants full_world_model --stage1-mode full --stage1-epoch 3 --epochs $e"; done
# epoch-0.75 는 HF 에 없어 제외 (위 "재고 현실" 참조)
for e in 0 0.25 0.5 1 2 3; do add "--variants lora_world_model --stage1-mode full --stage1-epoch 3 --epochs $e"; done
for e in 0 1 2;             do add "--variants lora_world_model --stage1-mode full --stage1-epoch 1 --epochs $e"; done
for e in 0 0.25 0.5 0.76 1; do add "--variants full_world_model --stage1-variant inverse-mix --stage1-mode full --stage1-epoch 3 --epochs $e"; done
for s1 in 0.5 1 1.5 2.01 2.51; do add "--variants full_world_model --stage1-variant inverse-mix --stage1-mode full --stage1-epoch $s1 --epochs 0"; done
for s1 in 1 3; do add "--variants lora_world_model --stage1-variant action-only --stage1-mode full --stage1-epoch $s1 --epochs 0"; done

echo "[sweep] 작업 ${#TASKS[@]} 개 (= invocation), leaf ${#TASKS[@]}×5 = $(( ${#TASKS[@]} * 5 ))"
echo "[sweep] GPU ${GPUS} (${NGPU} 장) · 로그 $LOGDIR"

if (( DRY_RUN )); then
  for i in "${!TASKS[@]}"; do
    printf "  gpu%-3s %s\n" "${GPU_ARR[$(( i % NGPU ))]}" "${TASKS[$i]}"
  done
  (( INCLUDE_LR )) && echo "  (manual) lora_base_lr-1e-04 / epoch-1"
  exit 0
fi

# ── lr-1e-04 유닛 (셸이 repo id 를 조립 못 해 직접 실행) ─────────────────────
# 별도 백그라운드로 띄우면 **같은 GPU 에 vLLM 두 개**가 올라가 서로 메모리를 못 잡는다
# (gpu_memory_utilization 0.80 × 2). 그래서 첫 GPU 워커의 **마지막 작업으로 인라인** 실행한다.
run_lr_unit() {
  local gpu="$1"
  # PATH 에 conda bin 이 없으면 vLLM 의 flashinfer JIT 컴파일이 `ninja` 를 못 찾아
  # "Engine core initialization failed" 로 죽는다 (2026-09-08·09-09 두 번 다 실측 —
  # stage2_eval.sh 경유 leaf 165 개는 _common.sh 가 PATH 를 잡아줘서 안 걸렸다).
  export PATH="$CONDA_ENV/bin:$PATH"
  local HUB="SaFD-00/qwen2.5-vl-3b-ac-exp08-base-lr-1e-04-stage2-lora-epoch1"
  local OUT_BASE="$REPO/outputs/AndroidControl_EXP08/eval/qwen2.5-vl-3b/stage2_eval/lora_base_lr-1e-04/epoch-1"
  local b leaf out test_jsonl
  for b in s1_id s1_ood s2_id s2_ood ood; do
    leaf="action-${b//_/-}"
    out="$OUT_BASE/on-AC_EXP08-$leaf"
    if [[ -f "$out/action_metrics.json" ]]; then echo "[lr] skip $leaf (완료)"; continue; fi
    mkdir -p "$out"
    test_jsonl="$REPO/data/AndroidControl_EXP08/action_test_$b.jsonl"
    echo "[lr] $(date +%H:%M:%S) $leaf"
    ( cd "$REPO/LlamaFactory" && \
      CUDA_VISIBLE_DEVICES="$gpu" PYTHONPATH="$REPO/LlamaFactory/src" \
      "$CONDA_ENV/bin/python" scripts/vllm_infer.py \
        --model_name_or_path "$HUB" \
        --dataset "IWM-AC_EXP08_action_test_$b" \
        --dataset_dir "$REPO/configs/lf_dataset" \
        --template qwen2_vl --cutoff_len 24576 --max_new_tokens 2048 \
        --image_max_pixels 1605632 --seed 42 \
        --vllm_config '{"gpu_memory_utilization": 0.80, "tensor_parallel_size": 1, "mm_processor_kwargs": {"min_pixels": 3136, "max_pixels": 1605632}}' \
        --save_name "$out/generated_predictions.jsonl" \
        --matrix_save_name "$out/predict_results.json" ) && \
    "$CONDA_ENV/bin/python" "$REPO/scripts/_action_eval.py" score \
        --test "$test_jsonl" --pred "$out/generated_predictions.jsonl" \
        --coord-mode xy --xml-schema cerebra \
        --output "$out/action_metrics.json" && \
    "$CONDA_ENV/bin/python" "$REPO/scripts/thought_eval.py" \
        --pred "$out/generated_predictions.jsonl" \
        --output "$out/thought_metrics.json" || echo "[lr] FAILED $leaf"
  done
  echo "[lr] ALL DONE"
}

# ── GPU 별 워커 ──────────────────────────────────────────────────────────────
for g in "${!GPU_ARR[@]}"; do
  gpu="${GPU_ARR[$g]}"
  (
    for i in "${!TASKS[@]}"; do
      (( i % NGPU == g )) || continue
      echo "[gpu$gpu] $(date +%H:%M:%S) start: ${TASKS[$i]}"
      # shellcheck disable=SC2086
      CUDA_VISIBLE_DEVICES="$gpu" \
      CONDA_PREFIX="$CONDA_ENV" \
      PYTHONPATH="$REPO/LlamaFactory/src${PYTHONPATH:+:$PYTHONPATH}" \
      LF_CUDA_GUARD_SKIP=1 \
        bash "$REPO/scripts/stage2_eval.sh" \
          --model qwen2.5-vl-3b --train-dataset AC_EXP08 --eval-datasets AC_EXP08 \
          ${TASKS[$i]} \
        || echo "[gpu$gpu] FAILED: ${TASKS[$i]}"
      echo "[gpu$gpu] $(date +%H:%M:%S) done:  ${TASKS[$i]}"
    done
    if (( INCLUDE_LR )) && (( g == 0 )); then
      echo "[gpu$gpu] $(date +%H:%M:%S) lr-1e-04 유닛 시작 (이 워커의 마지막 작업)"
      run_lr_unit "$gpu"
    fi
    echo "[gpu$gpu] ALL DONE"
  ) > "$LOGDIR/gpu${gpu}.log" 2>&1 &
  echo "[sweep] gpu$gpu 워커 pid=$!"
done

wait
echo "[sweep] 전체 완료. 진행 확인: grep -c 'done:' $LOGDIR/gpu*.log"
