#!/usr/bin/env bash
# Stage 1 Evaluation Pipeline (gui-model.ipynb Cell 21 + 23 + 24)
#   5A. Eval-Loss (llamafactory-cli train + do_eval) — base / full_world_model
#   5B. Hungarian Matching (vllm_infer.py 생성) — base (Zero-shot) / full_world_model

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

for DS in "${DATASETS[@]}"; do
  PREFIX="${DS_PREFIX[$DS]}"
  SLUG="${HF_SLUG[$DS]}"
  DS_TEST="${PREFIX}_stage1_test"
  HF_S1_MODEL="SaFD-00/qwen3-vl-8b-${SLUG}stage1-world-model"
  S1_EVAL_OUT="outputs/${DS}/stage1_eval"

  # ─────────────────────────────────────────────────────────────────────
  # 5A. Eval-Loss (Cell 21)
  # ─────────────────────────────────────────────────────────────────────
  for VARIANT in base full_world_model; do
    YAML="examples/custom/GUI-Model-${DS}/stage1_eval/${VARIANT}/eval_loss.yaml"
    require_yaml "$YAML" "run notebook Cell 20 to generate this YAML"
    run_logged "${SCRIPT_TAG}_${DS}_evalloss_${VARIANT}" \
      bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML'"
  done

  # ─────────────────────────────────────────────────────────────────────
  # 5B-1. Hungarian Matching — Baseline (Zero-shot) (Cell 23)
  # ─────────────────────────────────────────────────────────────────────
  HUNG_BASE="${S1_EVAL_OUT}/hungarian_matching/base"
  run_logged "${SCRIPT_TAG}_${DS}_hungarian_base" \
    bash -c "cd '$LF_ROOT' && mkdir -p '$HUNG_BASE' && \
      python scripts/vllm_infer.py \
        --model_name_or_path Qwen/Qwen3-VL-8B-Instruct \
        --dataset '$DS_TEST' \
        ${VLLM_COMMON_ARGS[*]} \
        --save_name '$HUNG_BASE/generated_predictions.jsonl' \
        --matrix_save_name '$HUNG_BASE/predict_results.json'"

  # ─────────────────────────────────────────────────────────────────────
  # 5B-2. Hungarian Matching — Full World Model (Fine-tuned) (Cell 24)
  # ─────────────────────────────────────────────────────────────────────
  HUNG_FWM="${S1_EVAL_OUT}/hungarian_matching/full_world_model"
  run_logged "${SCRIPT_TAG}_${DS}_hungarian_fwm" \
    bash -c "cd '$LF_ROOT' && mkdir -p '$HUNG_FWM' && \
      python scripts/vllm_infer.py \
        --model_name_or_path '$HF_S1_MODEL' \
        --dataset '$DS_TEST' \
        ${VLLM_COMMON_ARGS[*]} \
        --save_name '$HUNG_FWM/generated_predictions.jsonl' \
        --matrix_save_name '$HUNG_FWM/predict_results.json'"
done
