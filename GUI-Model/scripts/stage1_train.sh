#!/usr/bin/env bash
# Stage 1 Fine-tuning (full / lora)
# - --stage1-mode full (default) 또는 lora 로 선택.
# - FORCE_TORCHRUN=1 NPROC_PER_NODE=${NPROC_PER_NODE} (DeepSpeed Z3)
#
# NPROC_PER_NODE 은 .env 에서 관리 (기본값 2).

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1
: "${NPROC_PER_NODE:=2}"

SCRIPT_TAG="stage1_train_${STAGE1_MODE}"

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    YAML="examples/custom/GUI-Model-${DS}/stage1_${STAGE1_MODE}/${MODEL_SHORT}_world-model.yaml"
    require_yaml "$YAML" "run notebook Cell 9 to generate this YAML"

    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}" \
      env FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE="$NPROC_PER_NODE" \
      bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML'"
  done
done
