#!/usr/bin/env bash
# Stage 1 Evaluation Pipeline — Hungarian F1 기반 Best Epoch 자동 선택
#
#   Phase A. Baseline Hungarian (zero-shot Qwen3-VL-8B-Instruct)
#   Phase B. 전체 checkpoint sweep (vllm_infer.py → _hungarian_eval.py score)
#   Phase C. Winner 선택 (_hungarian_eval.py select) → BEST_CHECKPOINT 파일 기록
#
# 산출물:
#   saves/{DS}/stage1_eval/hungarian_matching/base/(generated_predictions|hungarian_metrics).json
#   saves/{DS}/stage1_eval/hungarian_matching/checkpoint-N/(generated_predictions|hungarian_metrics).json
#   saves/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT       (plain text)
#   saves/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT.json  (상세 순위)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_dataset_arg "${1:-all}"

SCRIPT_TAG="stage1_eval"
VLLM_COMMON_ARGS=(
  --template qwen3_vl_nothink
  --cutoff_len 8192
  --image_max_pixels 4233600
  --enable_thinking False
)

# DS 코드 → data/ 아래 디렉토리명
declare -A DS_DATADIR=( [MB]="MobiBench" [AC]="AndroidControl" )

for DS in "${DATASETS[@]}"; do
  PREFIX="${DS_PREFIX[$DS]}"
  DS_TEST="${PREFIX}_stage1_test"
  TEST_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage1_test.jsonl"
  TRAIN_DIR_REL="saves/${DS}/stage1_full/full_world_model"
  EVAL_DIR_REL="saves/${DS}/stage1_eval/hungarian_matching"
  TRAIN_DIR="$LF_ROOT/$TRAIN_DIR_REL"
  EVAL_DIR="$LF_ROOT/$EVAL_DIR_REL"

  if [ ! -f "$TEST_JSONL" ]; then
    echo "[!] [$DS] Missing test file: $TEST_JSONL" >&2
    exit 1
  fi

  # ─────────────────────────────────────────────────────────────────────
  # Phase A. Baseline Hungarian — Zero-shot Qwen3-VL-8B-Instruct
  # ─────────────────────────────────────────────────────────────────────
  OUT_BASE_REL="${EVAL_DIR_REL}/base"
  OUT_BASE="$LF_ROOT/$OUT_BASE_REL"
  run_logged "${SCRIPT_TAG}_${DS}_baseline" \
    bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_BASE_REL' && \
      python scripts/vllm_infer.py \
        --model_name_or_path Qwen/Qwen3-VL-8B-Instruct \
        --dataset '$DS_TEST' \
        ${VLLM_COMMON_ARGS[*]} \
        --save_name        '$OUT_BASE_REL/generated_predictions.jsonl' \
        --matrix_save_name '$OUT_BASE_REL/predict_results.json' && \
      python '$BASE_DIR/scripts/_hungarian_eval.py' score \
        --test   '$TEST_JSONL' \
        --pred   '$OUT_BASE/generated_predictions.jsonl' \
        --output '$OUT_BASE/hungarian_metrics.json'"

  # ─────────────────────────────────────────────────────────────────────
  # Phase B. Checkpoint sweep — saves/{DS}/stage1_full/full_world_model/checkpoint-*/
  # ─────────────────────────────────────────────────────────────────────
  shopt -s nullglob
  CKPTS=("$TRAIN_DIR"/checkpoint-*/)
  shopt -u nullglob
  if [ "${#CKPTS[@]}" -eq 0 ]; then
    echo "[!] [$DS] No checkpoints under $TRAIN_DIR (did stage1_train.sh complete?)" >&2
    exit 1
  fi
  echo "[+] [$DS] Sweeping ${#CKPTS[@]} checkpoints" >&2

  for CKPT_DIR in "${CKPTS[@]}"; do
    CKPT_NAME=$(basename "$CKPT_DIR")     # checkpoint-1055
    OUT_CKPT_REL="${EVAL_DIR_REL}/${CKPT_NAME}"
    OUT_CKPT="$LF_ROOT/$OUT_CKPT_REL"
    # LLaMA-Factory 는 cwd=LF_ROOT 기준 상대경로를 쓰므로 MODEL_PATH 도 상대로.
    MODEL_REL="${TRAIN_DIR_REL}/${CKPT_NAME}"

    run_logged "${SCRIPT_TAG}_${DS}_${CKPT_NAME}" \
      bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_CKPT_REL' && \
        python scripts/vllm_infer.py \
          --model_name_or_path '$MODEL_REL' \
          --dataset '$DS_TEST' \
          ${VLLM_COMMON_ARGS[*]} \
          --save_name        '$OUT_CKPT_REL/generated_predictions.jsonl' \
          --matrix_save_name '$OUT_CKPT_REL/predict_results.json' && \
        python '$BASE_DIR/scripts/_hungarian_eval.py' score \
          --test   '$TEST_JSONL' \
          --pred   '$OUT_CKPT/generated_predictions.jsonl' \
          --output '$OUT_CKPT/hungarian_metrics.json'"
  done

  # ─────────────────────────────────────────────────────────────────────
  # Phase C. Winner 선택 → BEST_CHECKPOINT 파일
  # ─────────────────────────────────────────────────────────────────────
  run_logged "${SCRIPT_TAG}_${DS}_select" \
    python "$BASE_DIR/scripts/_hungarian_eval.py" select \
      --eval-dir  "$EVAL_DIR" \
      --train-dir "$TRAIN_DIR" \
      --metric    avg_hungarian_f1
done
