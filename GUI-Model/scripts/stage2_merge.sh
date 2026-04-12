#!/usr/bin/env bash
# Stage 2 Merge & HuggingFace Hub Upload (gui-model.ipynb Cell 35 + 36)
#   merge_base.yaml         - Qwen/Qwen3-VL-8B-Instruct + lora_base         → stage2-base
#   merge_world_model.yaml  - stage1-world-model       + lora_world_model   → stage2-world-model
# 요구: HF_TOKEN 환경변수 또는 `.env` 설정

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_dataset_arg "${1:-all}"

SCRIPT_TAG="stage2_merge"

for DS in "${DATASETS[@]}"; do
  for VARIANT in merge_base merge_world_model; do
    YAML="examples/merge_custom/GUI-Model-${DS}/stage2/${VARIANT}.yaml"
    require_yaml "$YAML" "run notebook Cell 34 to generate this YAML"

    run_logged "${SCRIPT_TAG}_${DS}_${VARIANT}" \
      bash -c "cd '$LF_ROOT' && llamafactory-cli export '$YAML'"
  done
done
