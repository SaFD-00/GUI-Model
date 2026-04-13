#!/usr/bin/env bash
# Stage 2 Evaluation Pipeline — per-checkpoint sweep + winner 자동 선택
#
# 3-Way:
#   base              - Qwen/Qwen3-VL-8B-Instruct                   (Zero-shot baseline, 1회)
#   lora_base         - base + saves/{DS}/stage2_lora/lora_base/checkpoint-*          (sweep)
#   lora_world_model  - stage1_merged + saves/{DS}/stage2_lora/lora_world_model/checkpoint-* (sweep)
#
# 각 lora 변형별로:
#   Phase B. 체크포인트 sweep (vllm_infer + _action_eval.py score)
#   Phase C. winner 선택 (_action_eval.py select) → BEST_CHECKPOINT 파일 기록
#
# 전제: stage1_merge.sh 가 outputs/{DS}/stage1_merged/ 를 생성 (lora_world_model variant 의존)
#       stage2_train.sh 가 checkpoint-* 를 생성

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_dataset_arg "${1:-all}"

SCRIPT_TAG="stage2_eval"
VLLM_COMMON_ARGS=(
  --template qwen3_vl_nothink
  --cutoff_len 8192
  --image_max_pixels 4233600
  --enable_thinking False
)

declare -A DS_DATADIR=( [MB]="MobiBench" [AC]="AndroidControl" )

for DS in "${DATASETS[@]}"; do
  PREFIX="${DS_PREFIX[$DS]}"
  DS_TEST="${PREFIX}_stage2_test"
  TEST_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage2_test.jsonl"
  S2_EVAL_OUT_REL="saves/${DS}/stage2_eval"

  BASE_MODEL="Qwen/Qwen3-VL-8B-Instruct"
  STAGE1_MERGED="./outputs/${DS}/stage1_merged"

  if [ ! -f "$TEST_JSONL" ]; then
    echo "[!] [$DS] Missing test file: $TEST_JSONL" >&2
    exit 1
  fi
  if [ ! -d "$LF_ROOT/${STAGE1_MERGED#./}" ]; then
    echo "[!] [$DS] Missing $LF_ROOT/${STAGE1_MERGED#./} — run stage1_merge.sh first." >&2
    exit 1
  fi

  # ─────────────────────────────────────────────────────────────────────
  # Phase A. Baseline Zero-shot — vllm_infer (1회)
  # ─────────────────────────────────────────────────────────────────────
  OUT_BASE_REL="${S2_EVAL_OUT_REL}/base"
  OUT_BASE="$LF_ROOT/$OUT_BASE_REL"
  run_logged "${SCRIPT_TAG}_${DS}_baseline" \
    bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_BASE_REL' && \
      python scripts/vllm_infer.py \
        --model_name_or_path '$BASE_MODEL' \
        --dataset '$DS_TEST' \
        ${VLLM_COMMON_ARGS[*]} \
        --save_name        '$OUT_BASE_REL/generated_predictions.jsonl' \
        --matrix_save_name '$OUT_BASE_REL/predict_results.json' && \
      python '$BASE_DIR/scripts/_action_eval.py' score \
        --test   '$TEST_JSONL' \
        --pred   '$OUT_BASE/generated_predictions.jsonl' \
        --output '$OUT_BASE/action_metrics.json'"

  # ─────────────────────────────────────────────────────────────────────
  # Phase B + C. lora_base / lora_world_model 각각 checkpoint sweep + winner 선택
  # ─────────────────────────────────────────────────────────────────────
  declare -A VARIANT_BASE=(
    [lora_base]="$BASE_MODEL"
    [lora_world_model]="$STAGE1_MERGED"
  )

  for VARIANT in lora_base lora_world_model; do
    MODEL="${VARIANT_BASE[$VARIANT]}"
    LORA_DIR_REL="saves/${DS}/stage2_lora/${VARIANT}"
    LORA_DIR="$LF_ROOT/$LORA_DIR_REL"
    EVAL_DIR_REL="${S2_EVAL_OUT_REL}/${VARIANT}"
    EVAL_DIR="$LF_ROOT/$EVAL_DIR_REL"

    shopt -s nullglob
    CKPTS=("$LORA_DIR"/checkpoint-*/)
    shopt -u nullglob
    if [ "${#CKPTS[@]}" -eq 0 ]; then
      echo "[!] [$DS][$VARIANT] No checkpoints under $LORA_DIR — run stage2_train.sh first." >&2
      exit 1
    fi
    echo "[+] [$DS][$VARIANT] Sweeping ${#CKPTS[@]} checkpoints" >&2

    # Phase B: 체크포인트별 predict + score
    for CKPT_DIR in "${CKPTS[@]}"; do
      CKPT_NAME=$(basename "$CKPT_DIR")
      OUT_CKPT_REL="${EVAL_DIR_REL}/${CKPT_NAME}"
      OUT_CKPT="$LF_ROOT/$OUT_CKPT_REL"
      ADAPTER_REL="./${LORA_DIR_REL}/${CKPT_NAME}"

      run_logged "${SCRIPT_TAG}_${DS}_${VARIANT}_${CKPT_NAME}" \
        bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_CKPT_REL' && \
          python scripts/vllm_infer.py \
            --model_name_or_path   '$MODEL' \
            --adapter_name_or_path '$ADAPTER_REL' \
            --dataset '$DS_TEST' \
            ${VLLM_COMMON_ARGS[*]} \
            --save_name        '$OUT_CKPT_REL/generated_predictions.jsonl' \
            --matrix_save_name '$OUT_CKPT_REL/predict_results.json' && \
          python '$BASE_DIR/scripts/_action_eval.py' score \
            --test   '$TEST_JSONL' \
            --pred   '$OUT_CKPT/generated_predictions.jsonl' \
            --output '$OUT_CKPT/action_metrics.json'"
    done

    # Phase C: winner 선택 → BEST_CHECKPOINT 기록 (lora 출력 디렉토리에)
    run_logged "${SCRIPT_TAG}_${DS}_${VARIANT}_select" \
      python "$BASE_DIR/scripts/_action_eval.py" select \
        --eval-dir  "$EVAL_DIR" \
        --train-dir "$LORA_DIR" \
        --metric    overall_score
  done
done
