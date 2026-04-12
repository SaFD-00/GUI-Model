#!/usr/bin/env bash
# Stage 2 Evaluation Pipeline (gui-model.ipynb Cell 38 + 39 + 40)
#   3-way prediction via vllm_infer.py:
#     base              - Qwen/Qwen3-VL-8B-Instruct   (Zero-shot baseline)
#     lora_base         - SaFD-00/...-stage2-base
#     lora_world_model  - SaFD-00/...-stage2-world-model

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

for DS in "${DATASETS[@]}"; do
  PREFIX="${DS_PREFIX[$DS]}"
  SLUG="${HF_SLUG[$DS]}"
  DS_TEST="${PREFIX}_stage2_test"
  S2_EVAL_OUT="outputs/${DS}/stage2_eval"

  HF_S2_BASE="SaFD-00/qwen3-vl-8b-${SLUG}stage2-base"
  HF_S2_WORLD="SaFD-00/qwen3-vl-8b-${SLUG}stage2-world-model"

  # variant 이름 → (모델, 출력 서브디렉토리)
  declare -A MODELS=(
    [base]="Qwen/Qwen3-VL-8B-Instruct"
    [lora_base]="$HF_S2_BASE"
    [lora_world_model]="$HF_S2_WORLD"
  )

  for VARIANT in base lora_base lora_world_model; do
    MODEL="${MODELS[$VARIANT]}"
    OUT="${S2_EVAL_OUT}/${VARIANT}"

    run_logged "${SCRIPT_TAG}_${DS}_${VARIANT}" \
      bash -c "cd '$LF_ROOT' && mkdir -p '$OUT' && \
        python scripts/vllm_infer.py \
          --model_name_or_path '$MODEL' \
          --dataset '$DS_TEST' \
          ${VLLM_COMMON_ARGS[*]} \
          --save_name '$OUT/generated_predictions.jsonl' \
          --matrix_save_name '$OUT/predict_results.json'"
  done
done
