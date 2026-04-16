#!/usr/bin/env bash
# Stage 1 Full Fine-tuning
# - 지정 모델 + World Modeling data
# - FORCE_TORCHRUN=1 NPROC_PER_NODE=4 (H100 x 4, DeepSpeed Z3)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"

SCRIPT_TAG="stage1_train"

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    YAML="examples/train_custom/GUI-Model-${DS}/stage1_full/${MODEL_SHORT}.yaml"
    require_yaml "$YAML" "run notebook Cell 8 to generate this YAML"

    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}" \
      env FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=4 \
      bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML'"
  done
done
