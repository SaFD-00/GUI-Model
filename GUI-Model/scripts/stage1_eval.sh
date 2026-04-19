#!/usr/bin/env bash
# Stage 1 Evaluation Pipeline — 로컬 checkpoint sweep + Hungarian winner
#
# --stage1-mode full (default) | lora 에 따라 sweep 대상 adapter 디렉토리와 모델 로딩 방식을 분기한다.
#
#   Phase A. Baseline Hungarian (zero-shot, $BASE_MODEL) — mode 무관
#   Phase B. outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/checkpoint-*/ sweep
#              - full: --model_name_or_path <checkpoint_dir>
#              - lora: --model_name_or_path $BASE_MODEL --adapter_name_or_path <checkpoint_dir>
#   Phase C. Winner 선택 (_hungarian_eval.py select) → BEST_CHECKPOINT 파일 기록
#
# 산출물 (BASE_DIR 기준):
#   outputs/{DS}/eval/{MODEL}/stage1_eval/base/(generated_predictions|hungarian_metrics).json
#   outputs/{DS}/eval/{MODEL}/stage1_eval/${MODE}_world_model/checkpoint-N/(generated_predictions|hungarian_metrics).json
#   outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/BEST_CHECKPOINT       (plain text)
#   outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/BEST_CHECKPOINT.json  (상세 순위)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage1_eval_${STAGE1_MODE}"
# notebook _DATASET_CONFIG.stage1.lora_rank (MB=AC=8)
STAGE1_LORA_RANK=8

# DS 코드 → data/ 아래 디렉토리명
declare -A DS_DATADIR=( [MB]="MobiBench" [AC]="AndroidControl" )

for MODEL_SHORT in "${MODELS[@]}"; do
  BASE_MODEL="${MODEL_ID[$MODEL_SHORT]}"
  TEMPLATE="${MODEL_TEMPLATE[$MODEL_SHORT]}"

  VLLM_COMMON_ARGS=(
    --template "$TEMPLATE"
    --cutoff_len 8192
    --image_max_pixels 4233600
  )
  if [[ "$TEMPLATE" == qwen3_vl* ]]; then
    VLLM_COMMON_ARGS+=(--enable_thinking False)
  fi

  if [ "$STAGE1_MODE" = "full" ]; then
    SWEEP_VLLM_CONFIG='{"gpu_memory_utilization": 0.80}'
  else
    SWEEP_VLLM_CONFIG="{\"gpu_memory_utilization\": 0.80, \"max_lora_rank\": ${STAGE1_LORA_RANK}}"
  fi

  for DS in "${DATASETS[@]}"; do
    PREFIX="${DS_PREFIX[$DS]}"
    DS_TEST="${PREFIX}_stage1_test"
    TEST_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage1_test.jsonl"
    TRAIN_DIR_REL="../outputs/${DS}/adapters/${MODEL_SHORT}_stage1_${STAGE1_MODE}"
    EVAL_DIR_REL="../outputs/${DS}/eval/${MODEL_SHORT}/stage1_eval"
    TRAIN_DIR="$LF_ROOT/$TRAIN_DIR_REL"
    EVAL_DIR="$LF_ROOT/$EVAL_DIR_REL"

    if [ ! -f "$TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test file: $TEST_JSONL" >&2
      exit 1
    fi

    # ─────────────────────────────────────────────────────────────────────
    # Phase A. Baseline Hungarian — Zero-shot (mode 무관)
    # ─────────────────────────────────────────────────────────────────────
    OUT_BASE_REL="${EVAL_DIR_REL}/base"
    OUT_BASE="$LF_ROOT/$OUT_BASE_REL"
    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_baseline" \
      bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_BASE_REL' && \
        python scripts/vllm_infer.py \
          --model_name_or_path '$BASE_MODEL' \
          --dataset '$DS_TEST' \
          --dataset_dir '$LF_ROOT/data' \
          ${VLLM_COMMON_ARGS[*]} \
          --vllm_config '{\"gpu_memory_utilization\": 0.80}' \
          --save_name        '$OUT_BASE_REL/generated_predictions.jsonl' \
          --matrix_save_name '$OUT_BASE_REL/predict_results.json' && \
        python '$BASE_DIR/scripts/_hungarian_eval.py' score \
          --test   '$TEST_JSONL' \
          --pred   '$OUT_BASE/generated_predictions.jsonl' \
          --output '$OUT_BASE/hungarian_metrics.json'"

    # ─────────────────────────────────────────────────────────────────────
    # Phase B. Checkpoint sweep — outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/checkpoint-*/
    # ─────────────────────────────────────────────────────────────────────
    shopt -s nullglob
    CKPTS=("$TRAIN_DIR"/checkpoint-*/)
    shopt -u nullglob
    if [ "${#CKPTS[@]}" -eq 0 ]; then
      echo "[!] [$MODEL_SHORT][$DS][$STAGE1_MODE] No checkpoints under $TRAIN_DIR (did stage1_train.sh --stage1-mode ${STAGE1_MODE} complete?)" >&2
      exit 1
    fi
    echo "[+] [$MODEL_SHORT][$DS][$STAGE1_MODE] Sweeping ${#CKPTS[@]} checkpoints" >&2

    for CKPT_DIR in "${CKPTS[@]}"; do
      CKPT_NAME=$(basename "$CKPT_DIR")
      OUT_CKPT_REL="${EVAL_DIR_REL}/${STAGE1_MODE}_world_model/${CKPT_NAME}"
      OUT_CKPT="$LF_ROOT/$OUT_CKPT_REL"
      CKPT_REL="${TRAIN_DIR_REL}/${CKPT_NAME}"

      if [ "$STAGE1_MODE" = "full" ]; then
        MODEL_ARGS="--model_name_or_path '$CKPT_REL'"
      else
        MODEL_ARGS="--model_name_or_path '$BASE_MODEL' --adapter_name_or_path '$CKPT_REL'"
      fi

      run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${CKPT_NAME}" \
        bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_CKPT_REL' && \
          python scripts/vllm_infer.py \
            ${MODEL_ARGS} \
            --dataset '$DS_TEST' \
            --dataset_dir '$LF_ROOT/data' \
            ${VLLM_COMMON_ARGS[*]} \
            --vllm_config '${SWEEP_VLLM_CONFIG}' \
            --save_name        '$OUT_CKPT_REL/generated_predictions.jsonl' \
            --matrix_save_name '$OUT_CKPT_REL/predict_results.json' && \
          python '$BASE_DIR/scripts/_hungarian_eval.py' score \
            --test   '$TEST_JSONL' \
            --pred   '$OUT_CKPT/generated_predictions.jsonl' \
            --output '$OUT_CKPT/hungarian_metrics.json'"
    done

    # ─────────────────────────────────────────────────────────────────────
    # Phase C. Winner 선택 → BEST_CHECKPOINT 파일 (adapter 디렉토리에 기록)
    # ─────────────────────────────────────────────────────────────────────
    WIN_EVAL_DIR="$EVAL_DIR/${STAGE1_MODE}_world_model"
    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_select" \
      python "$BASE_DIR/scripts/_hungarian_eval.py" select \
        --eval-dir  "$WIN_EVAL_DIR" \
        --train-dir "$TRAIN_DIR" \
        --metric    avg_hungarian_f1
  done
done
