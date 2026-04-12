#!/usr/bin/env bash
# Stage 1 Full Fine-tuning (gui-model.ipynb Cell 15)
# - Qwen3-VL-8B-Instruct + World Modeling data
# - FORCE_TORCHRUN=1 NPROC_PER_NODE=4 (H100 x 4, DeepSpeed Z3)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_dataset_arg "${1:-all}"

SCRIPT_TAG="stage1_train"

for DS in "${DATASETS[@]}"; do
  YAML="examples/custom/GUI-Model-${DS}/stage1_full/qwen3_vl_8b_gui.yaml"
  require_yaml "$YAML" "run notebook Cell 14 to generate this YAML"

  run_logged "${SCRIPT_TAG}_${DS}" \
    env FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=4 \
    bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML'"
done
