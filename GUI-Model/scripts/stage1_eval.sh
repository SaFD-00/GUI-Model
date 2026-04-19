#!/usr/bin/env bash
# Stage 1 Evaluation Pipeline — HF Hub 업로드된 merged world model 평가
#
# --stage1-mode full (default) | lora 선택 가능.
#
#   Phase A. Baseline Hungarian (zero-shot, $BASE_MODEL)
#   Phase B. HF merged world model (SaFD-00/{short}-{slug}stage1-${MODE}-world-model) Hungarian
#
# HF 업로드본은 stage1_merge.sh 에서 선정·merge 된 winner 체크포인트이므로
# 로컬 checkpoint sweep / winner 선택 단계는 불필요하다.
#
# 산출물 (BASE_DIR 기준):
#   outputs/{DS}/eval/{MODEL}/stage1_eval/base/(generated_predictions|hungarian_metrics).json
#   outputs/{DS}/eval/{MODEL}/stage1_eval/${MODE}_world_model/(generated_predictions|hungarian_metrics).json

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage1_eval_${STAGE1_MODE}"

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
    EVAL_DIR_REL="../outputs/${DS}/eval/${MODEL_SHORT}/stage1_eval"

    HF_S1_MODEL="SaFD-00/${MODEL_SHORT}-${HF_SLUG[$DS]}stage1-${STAGE1_MODE}-world-model"

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
    # Phase B. HF merged World Model — $HF_S1_MODEL (mode-tagged)
    # ─────────────────────────────────────────────────────────────────────
    OUT_WM_REL="${EVAL_DIR_REL}/${STAGE1_MODE}_world_model"
    OUT_WM="$LF_ROOT/$OUT_WM_REL"
    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${STAGE1_MODE}_world_model" \
      bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_WM_REL' && \
        python scripts/vllm_infer.py \
          --model_name_or_path '$HF_S1_MODEL' \
          --dataset '$DS_TEST' \
          --dataset_dir '$LF_ROOT/data' \
          ${VLLM_COMMON_ARGS[*]} \
          --vllm_config '{\"gpu_memory_utilization\": 0.80}' \
          --save_name        '$OUT_WM_REL/generated_predictions.jsonl' \
          --matrix_save_name '$OUT_WM_REL/predict_results.json' && \
        python '$BASE_DIR/scripts/_hungarian_eval.py' score \
          --test   '$TEST_JSONL' \
          --pred   '$OUT_WM/generated_predictions.jsonl' \
          --output '$OUT_WM/hungarian_metrics.json'"
  done
done
