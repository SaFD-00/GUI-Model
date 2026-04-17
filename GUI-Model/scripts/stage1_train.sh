#!/usr/bin/env bash
# Stage 1 Full Fine-tuning
# - backend=llamafactory: FORCE_TORCHRUN=1 NPROC_PER_NODE=${NPROC_PER_NODE} (DeepSpeed Z3)
# - backend=unsloth:      accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE}
#
# NPROC_PER_NODE 은 .env 에서 관리 (기본값 2).
# Backend 매핑은 _common.sh::MODEL_BACKEND 에 정의되어 있으며
# 미지정 모델은 기본값 "llamafactory" 로 분기된다.

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1
: "${NPROC_PER_NODE:=2}"

SCRIPT_TAG="stage1_train"

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    BACKEND="$(get_backend "$MODEL_SHORT")"

    case "$BACKEND" in
      llamafactory)
        YAML="examples/train_custom/GUI-Model-${DS}/stage1_full/${MODEL_SHORT}.yaml"
        require_yaml "$YAML" "run notebook Cell 8 to generate this YAML"

        run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}" \
          env FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE="$NPROC_PER_NODE" \
          bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML'"
        ;;

      unsloth)
        YAML="$BASE_DIR/unsloth/configs/GUI-Model-${DS}/stage1_full/${MODEL_SHORT}.yaml"
        if [ ! -f "$YAML" ]; then
          echo "[!] Missing Unsloth YAML: $YAML" >&2
          echo "    Hint: run notebook Cell 8 to generate this YAML" >&2
          exit 1
        fi

        run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}" \
          bash -c "cd '$BASE_DIR' && accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE} \
            scripts/_unsloth_train.py --config '$YAML' --base-dir '$BASE_DIR'"
        ;;

      *)
        echo "[!] Unknown backend '$BACKEND' for model $MODEL_SHORT" >&2
        exit 2
        ;;
    esac
  done
done
