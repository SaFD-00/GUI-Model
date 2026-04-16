#!/usr/bin/env bash
# Stage 2 LoRA Fine-tuning
#   [stage2]         base.yaml        - Base model + LoRA
#   [stage1+stage2]  world_model.yaml - Stage 1 merged model + LoRA
#
# Backend 매핑은 _common.sh::MODEL_BACKEND 에 정의된다.
# - backend=llamafactory: llamafactory-cli (단일 GPU 자동 감지, 원본 노트북과 동일)
# - backend=unsloth:      accelerate launch --multi_gpu --num_processes 4

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_train"

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    BACKEND="$(get_backend "$MODEL_SHORT")"

    for VARIANT in base world_model; do
      case "$BACKEND" in
        llamafactory)
          YAML="examples/train_custom/GUI-Model-${DS}/stage2_lora/${MODEL_SHORT}/${VARIANT}.yaml"
          require_yaml "$YAML" "run notebook Cell 12 to generate this YAML"

          run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
            bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML'"
          ;;

        unsloth)
          YAML="$BASE_DIR/configs/unsloth/GUI-Model-${DS}/stage2_lora/${MODEL_SHORT}/${VARIANT}.yaml"
          if [ ! -f "$YAML" ]; then
            echo "[!] Missing Unsloth YAML: $YAML" >&2
            echo "    Hint: run notebook Cell 12 to generate this YAML" >&2
            exit 1
          fi

          run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
            bash -c "cd '$BASE_DIR' && accelerate launch --multi_gpu --num_processes 4 \
              scripts/_unsloth_train.py --config '$YAML' --base-dir '$BASE_DIR'"
          ;;

        *)
          echo "[!] Unknown backend '$BACKEND' for model $MODEL_SHORT" >&2
          exit 2
          ;;
      esac
    done
  done
done
