#!/usr/bin/env bash
# Stage 2 LoRA Fine-tuning
#   [stage2]         base.yaml        - Base model + LoRA
#   [stage1+stage2]  world_model.yaml - Stage 1 merged model + LoRA
#
# NOTE: 노트북 원본에 FORCE_TORCHRUN prefix 없음 → 동일하게 미부여.
#       llamafactory-cli 가 단일 GPU 자동 감지로 실행됨.

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_train"

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    for VARIANT in base world_model; do
      YAML="examples/train_custom/GUI-Model-${DS}/stage2_lora/${MODEL_SHORT}/${VARIANT}.yaml"
      require_yaml "$YAML" "run notebook Cell 12 to generate this YAML"

      run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
        bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML'"
    done
  done
done
