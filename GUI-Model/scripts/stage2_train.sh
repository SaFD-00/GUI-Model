#!/usr/bin/env bash
# Stage 2 LoRA Fine-tuning — 2 variants:
#   [stage2]          base.yaml                     - Base model + LoRA
#   [stage1+stage2]   world-model-${STAGE1_MODE}.yaml - Stage 1 merged (full|lora) + LoRA
#
# --stage1-mode full (default) | lora 에 따라 world-model variant 의 상류 Stage 1 소스 선택.
#   full → world-model-full.yaml
#   lora → world-model-lora.yaml
# base variant 는 Stage 1 의존이 없으므로 항상 함께 학습된다.
#
# Stage 1 winner base 참조 (world-model variant 에만 적용):
#   stage1_eval.sh 가 작성한 adapters/{M}_stage1_{MODE}/BEST_CHECKPOINT.json 의
#   "epoch" 필드를 읽어 local merged/{M}_stage1_{MODE}/epoch-{Ewin}/ 을 base 로 사용.
#   YAML 의 model_name_or_path 라인을 런타임에 sed 로 재작성 (임시 YAML 사용).
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

# world-model variant 용: BEST_CHECKPOINT.json → Ewin 추출 → local merged path 반환.
resolve_stage1_winner_base() {
  local model_short="$1" ds="$2" mode="$3"
  local best_json="$BASE_DIR/outputs/${ds}/adapters/${model_short}_stage1_${mode}/BEST_CHECKPOINT.json"
  if [ ! -f "$best_json" ]; then
    echo "[!] Missing $best_json — run stage1_eval.sh --stage1-mode ${mode} first." >&2
    return 1
  fi
  local epoch
  epoch=$(python - "$best_json" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
e = d.get("epoch")
if e is None:
    sys.stderr.write("[!] BEST_CHECKPOINT.json has no 'epoch' field (legacy eval output?)\n")
    sys.exit(1)
print(int(e))
PY
) || return 1
  # stage2 YAML 은 LF_ROOT 또는 UNSLOTH_ROOT 를 cwd 로 실행되므로 "../outputs/..." 상대경로로 반환.
  echo "../outputs/${ds}/merged/${model_short}_stage1_${mode}/epoch-${epoch}"
}

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    BACKEND="$(get_backend "$MODEL_SHORT")"

    VARIANTS=("base" "world-model-${STAGE1_MODE}")

    for VARIANT in "${VARIANTS[@]}"; do
      case "$BACKEND" in
        llamafactory)
          YAML_REL="examples/custom/GUI-Model-${DS}/stage2_lora/${MODEL_SHORT}_${VARIANT}.yaml"
          YAML_ABS="$LF_ROOT/$YAML_REL"
          require_yaml "$YAML_REL" "run notebook Cell 15 to generate this YAML"
          RUN_YAML_REL="$YAML_REL"

          if [[ "$VARIANT" == world-model-* ]]; then
            WINNER_BASE=$(resolve_stage1_winner_base "$MODEL_SHORT" "$DS" "$STAGE1_MODE") || exit 1
            TMP_YAML=$(mktemp -t "stage2_train_${MODEL_SHORT}_${DS}_${VARIANT}_XXXXXX.yaml")
            # YAML 의 model_name_or_path 첫 매치를 winner local path 로 치환.
            sed "0,/^model_name_or_path:/{s|^model_name_or_path:.*|model_name_or_path: ${WINNER_BASE}|}" \
              "$YAML_ABS" > "$TMP_YAML"
            # LF_ROOT 아래로 심볼릭 링크해 상대 YAML 경로로 cli 에 전달 가능하게 함.
            LINK_REL="examples/custom/GUI-Model-${DS}/stage2_lora/.${MODEL_SHORT}_${VARIANT}.runtime.yaml"
            ln -sfn "$TMP_YAML" "$LF_ROOT/$LINK_REL"
            RUN_YAML_REL="$LINK_REL"
            echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Stage 1 winner base = $WINNER_BASE" >&2
            trap 'rm -f "$TMP_YAML" "$LF_ROOT/$LINK_REL"' RETURN
          fi

          run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
            bash -c "cd '$LF_ROOT' && llamafactory-cli train '$RUN_YAML_REL'"

          if [[ "$VARIANT" == world-model-* ]]; then
            rm -f "$TMP_YAML" "$LF_ROOT/$LINK_REL"
            trap - RETURN
          fi
          ;;

        unsloth)
          YAML_ABS="$BASE_DIR/unsloth/configs/GUI-Model-${DS}/stage2_lora/${MODEL_SHORT}_${VARIANT}.yaml"
          if [ ! -f "$YAML_ABS" ]; then
            echo "[!] Missing Unsloth YAML: $YAML_ABS" >&2
            echo "    Hint: run notebook Cell 17 to generate this YAML" >&2
            exit 1
          fi
          RUN_YAML="$YAML_ABS"

          if [[ "$VARIANT" == world-model-* ]]; then
            WINNER_BASE=$(resolve_stage1_winner_base "$MODEL_SHORT" "$DS" "$STAGE1_MODE") || exit 1
            TMP_YAML=$(mktemp -t "stage2_train_${MODEL_SHORT}_${DS}_${VARIANT}_XXXXXX.yaml")
            sed "0,/^model_name_or_path:/{s|^model_name_or_path:.*|model_name_or_path: ${WINNER_BASE}|}" \
              "$YAML_ABS" > "$TMP_YAML"
            RUN_YAML="$TMP_YAML"
            echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Stage 1 winner base = $WINNER_BASE" >&2
          fi

          run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
            bash -c "cd '$UNSLOTH_ROOT' && accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE} \
              ../scripts/_unsloth_train.py --config '$RUN_YAML' --base-dir '$UNSLOTH_ROOT'"

          if [[ "$VARIANT" == world-model-* ]]; then
            rm -f "$TMP_YAML"
          fi
          ;;

        *)
          echo "[!] Unknown backend '$BACKEND' for model $MODEL_SHORT" >&2
          exit 2
          ;;
      esac
    done
  done
done
