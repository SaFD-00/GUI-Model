#!/usr/bin/env bash
# Stage 2 LoRA Fine-tuning — 3 variants:
#   [stage2]          base.yaml                     - Base model + LoRA
#   [stage1+stage2]   world-model-${STAGE1_MODE}.yaml - Stage 1 merged (full|lora) + LoRA
#
# --stage1-mode full (default) | lora 에 따라 world-model variant 의 상류 Stage 1 소스 선택.
#   full → world-model-full.yaml
#   lora → world-model-lora.yaml
# base variant 는 Stage 1 의존이 없으므로 항상 함께 학습된다.
#
# NPROC_PER_NODE 은 .env 에서 관리 (기본값 2).
# Backend 매핑은 _common.sh::MODEL_BACKEND 에 정의된다.
# - backend=llamafactory: llamafactory-cli (단일 GPU 자동 감지, 원본 노트북과 동일)
# - backend=unsloth:      accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE}

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1
: "${NPROC_PER_NODE:=2}"

SCRIPT_TAG="stage2_train_from_${STAGE1_MODE}"

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    BACKEND="$(get_backend "$MODEL_SHORT")"

    # variant name 목록: base + upstream Stage 1 모드에 맞춘 world-model
    VARIANTS=("base" "world-model-${STAGE1_MODE}")

    for VARIANT in "${VARIANTS[@]}"; do
      case "$BACKEND" in
        llamafactory)
          YAML="examples/custom/GUI-Model-${DS}/stage2_lora/${MODEL_SHORT}_${VARIANT}.yaml"
          require_yaml "$YAML" "run notebook Cell 15 to generate this YAML"

          run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
            bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML'"
          ;;

        unsloth)
          YAML="$BASE_DIR/unsloth/configs/GUI-Model-${DS}/stage2_lora/${MODEL_SHORT}_${VARIANT}.yaml"
          if [ ! -f "$YAML" ]; then
            echo "[!] Missing Unsloth YAML: $YAML" >&2
            echo "    Hint: run notebook Cell 17 to generate this YAML" >&2
            exit 1
          fi

          run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
            bash -c "cd '$UNSLOTH_ROOT' && accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE} \
              ../scripts/_unsloth_train.py --config '$YAML' --base-dir '$UNSLOTH_ROOT'"
          ;;

        *)
          echo "[!] Unknown backend '$BACKEND' for model $MODEL_SHORT" >&2
          exit 2
          ;;
      esac
    done
  done
done
