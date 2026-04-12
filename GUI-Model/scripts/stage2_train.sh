#!/usr/bin/env bash
# Stage 2 LoRA Fine-tuning (gui-model.ipynb Cell 31 + 32)
#   [stage2]         base.yaml        - Qwen/Qwen3-VL-8B-Instruct + LoRA
#   [stage1+stage2]  world_model.yaml - Stage 1 merged model + LoRA
#
# NOTE: 노트북 Cell 31/32 원본에 FORCE_TORCHRUN prefix 없음 → 동일하게 미부여.
#       llamafactory-cli 가 단일 GPU 자동 감지로 실행됨.

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_dataset_arg "${1:-all}"

SCRIPT_TAG="stage2_train"

for DS in "${DATASETS[@]}"; do
  for VARIANT in base world_model; do
    YAML="examples/custom/GUI-Model-${DS}/stage2_lora/${VARIANT}.yaml"
    require_yaml "$YAML" "run notebook Cell 30 to generate this YAML"

    run_logged "${SCRIPT_TAG}_${DS}_${VARIANT}" \
      bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML'"
  done
done
