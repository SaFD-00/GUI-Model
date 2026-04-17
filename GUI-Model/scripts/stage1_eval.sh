#!/usr/bin/env bash
# Stage 1 Evaluation Pipeline — Hungarian F1 기반 Best Epoch 자동 선택
#
#   Phase A. Baseline Hungarian (zero-shot)
#   Phase B. 전체 checkpoint sweep (vllm_infer.py → _hungarian_eval.py score)
#   Phase C. Winner 선택 (_hungarian_eval.py select) → BEST_CHECKPOINT 파일 기록
#
# Backend 독립: Unsloth 로 학습한 체크포인트도 표준 HF safetensors 이므로
#               vllm_infer.py 가 프레임워크 무관하게 로드 가능하다.
#               (단, vLLM/transformers 가 해당 아키텍처를 지원해야 한다.)
#
# 산출물 (BASE_DIR 기준):
#   outputs/{DS}/eval/{MODEL}/stage1_eval/base/(generated_predictions|hungarian_metrics).json
#   outputs/{DS}/eval/{MODEL}/stage1_eval/checkpoint-N/(generated_predictions|hungarian_metrics).json
#   outputs/{DS}/adapters/{MODEL}/stage1_full_world_model/BEST_CHECKPOINT       (plain text)
#   outputs/{DS}/adapters/{MODEL}/stage1_full_world_model/BEST_CHECKPOINT.json  (상세 순위)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage1_eval"

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
  # Qwen3 계열만 enable_thinking 명시
  if [[ "$TEMPLATE" == qwen3_vl* ]]; then
    VLLM_COMMON_ARGS+=(--enable_thinking False)
  fi

  for DS in "${DATASETS[@]}"; do
    PREFIX="${DS_PREFIX[$DS]}"
    DS_TEST="${PREFIX}_stage1_test"
    TEST_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage1_test.jsonl"
    # LF cwd 기준 상대경로 (= BASE_DIR 기준 "outputs/..."). 최종 절대경로는 BASE_DIR/outputs/... 로 귀결.
    TRAIN_DIR_REL="../outputs/${DS}/adapters/${MODEL_SHORT}/stage1_full_world_model"
    EVAL_DIR_REL="../outputs/${DS}/eval/${MODEL_SHORT}/stage1_eval"
    TRAIN_DIR="$LF_ROOT/$TRAIN_DIR_REL"
    EVAL_DIR="$LF_ROOT/$EVAL_DIR_REL"

    if [ ! -f "$TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test file: $TEST_JSONL" >&2
      exit 1
    fi

    # ─────────────────────────────────────────────────────────────────────
    # Phase A. Baseline Hungarian — Zero-shot
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
    # Phase B. Checkpoint sweep — outputs/{DS}/adapters/{MODEL}/stage1_full_world_model/checkpoint-*/
    # ─────────────────────────────────────────────────────────────────────
    shopt -s nullglob
    CKPTS=("$TRAIN_DIR"/checkpoint-*/)
    shopt -u nullglob
    if [ "${#CKPTS[@]}" -eq 0 ]; then
      echo "[!] [$MODEL_SHORT][$DS] No checkpoints under $TRAIN_DIR (did stage1_train.sh complete?)" >&2
      exit 1
    fi
    echo "[+] [$MODEL_SHORT][$DS] Sweeping ${#CKPTS[@]} checkpoints" >&2

    for CKPT_DIR in "${CKPTS[@]}"; do
      CKPT_NAME=$(basename "$CKPT_DIR")     # checkpoint-1055
      OUT_CKPT_REL="${EVAL_DIR_REL}/${CKPT_NAME}"
      OUT_CKPT="$LF_ROOT/$OUT_CKPT_REL"
      MODEL_REL="${TRAIN_DIR_REL}/${CKPT_NAME}"

      run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${CKPT_NAME}" \
        bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_CKPT_REL' && \
          python scripts/vllm_infer.py \
            --model_name_or_path '$MODEL_REL' \
            --dataset '$DS_TEST' \
            --dataset_dir '$LF_ROOT/data' \
            ${VLLM_COMMON_ARGS[*]} \
          --vllm_config '{\"gpu_memory_utilization\": 0.80}' \
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
    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_select" \
      python "$BASE_DIR/scripts/_hungarian_eval.py" select \
        --eval-dir  "$EVAL_DIR" \
        --train-dir "$TRAIN_DIR" \
        --metric    avg_hungarian_f1
  done
done
