#!/usr/bin/env bash
# Stage 2 Merge & Upload — BEST_CHECKPOINT 기반 winner adapter 자동 선택
#
# 두 variant 각각:
#   merge_base          - Base model          + lora_base/<winner>
#   merge_world_model   - stage1_merged       + lora_world_model/<winner>
#
# Backend 분기 (_common.sh::MODEL_BACKEND):
#   - llamafactory: 임시 merge YAML 생성 → llamafactory-cli export
#   - unsloth:      scripts/_unsloth_merge.py --mode lora (merged_16bit safetensors)
#
# BEST_CHECKPOINT 없으면 해당 variant 를 [SKIP] 경고 후 건너뜀.
# 전제: .env 의 HF_TOKEN. stage1_merge.sh 가 outputs/{DS}/merged/{MODEL}/stage1_full_world_model/ 를 생성해둔 상태.

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_merge"
MERGED_COUNT=0
SKIPPED_COUNT=0

for MODEL_SHORT in "${MODELS[@]}"; do
  BASE_MODEL="${MODEL_ID[$MODEL_SHORT]}"
  BACKEND="$(get_backend "$MODEL_SHORT")"

  for DS in "${DATASETS[@]}"; do
    # LF cwd 기준 상대경로 (../outputs/...) + BASE_DIR 기준 절대경로 동시 유지.
    STAGE1_MERGED_REL="../outputs/${DS}/merged/${MODEL_SHORT}/stage1_full_world_model"
    STAGE1_MERGED="$BASE_DIR/outputs/${DS}/merged/${MODEL_SHORT}/stage1_full_world_model"

    if [ ! -d "$STAGE1_MERGED" ]; then
      echo "[SKIP] [$MODEL_SHORT][$DS] Missing $STAGE1_MERGED — stage1_merge.sh 미완료, 건너뜁니다." >&2
      SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
      continue
    fi

    declare -A BASE_PATH=(
      [merge_base]="$BASE_MODEL"
      [merge_world_model]="$STAGE1_MERGED"
    )
    declare -A BASE_PATH_LF_REL=(
      [merge_base]="$BASE_MODEL"
      [merge_world_model]="${STAGE1_MERGED_REL}"
    )
    declare -A LORA_DIR_REL=(
      [merge_base]="../outputs/${DS}/adapters/${MODEL_SHORT}/stage2_lora_base"
      [merge_world_model]="../outputs/${DS}/adapters/${MODEL_SHORT}/stage2_lora_world_model"
    )
    declare -A MERGED_DIR=(
      [merge_base]="stage2_lora_base"
      [merge_world_model]="stage2_lora_world_model"
    )
    declare -A HUB_SUFFIX_MAP=(
      [merge_base]="base"
      [merge_world_model]="world-model"
    )

    for VARIANT in merge_base merge_world_model; do
      LORA_REL="${LORA_DIR_REL[$VARIANT]}"
      BEST_FILE="$LF_ROOT/$LORA_REL/BEST_CHECKPOINT"

      if [ ! -f "$BEST_FILE" ]; then
        echo "[SKIP] [$MODEL_SHORT][$DS][$VARIANT] BEST_CHECKPOINT not found at $BEST_FILE — stage2 평가 미완료, 건너뜁니다." >&2
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
        continue
      fi

      CKPT_NAME=$(tr -d '[:space:]' < "$BEST_FILE")
      # LORA_REL 이 "../outputs/..." 형태이므로 절대경로는 BASE_DIR 기준으로 재구성.
      LORA_SUB="${LORA_REL#../}"              # "outputs/{DS}/adapters/{M}/stage2_lora_*"
      ADAPTER_ABS="$BASE_DIR/$LORA_SUB/$CKPT_NAME"
      ADAPTER_REL="${LORA_REL}/${CKPT_NAME}"
      echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Using Stage 2 winner: ${CKPT_NAME}" >&2

      HUB_ID="SaFD-00/${MODEL_SHORT}-${HF_SLUG[$DS]}stage2-${HUB_SUFFIX_MAP[$VARIANT]}"
      MERGED_REL="../outputs/${DS}/merged/${MODEL_SHORT}/${MERGED_DIR[$VARIANT]}"
      LOCAL_DIR="$BASE_DIR/outputs/${DS}/merged/${MODEL_SHORT}/${MERGED_DIR[$VARIANT]}"

      case "$BACKEND" in
        llamafactory)
          TMP_YAML=$(mktemp -t "stage2_merge_${MODEL_SHORT}_${DS}_${VARIANT}_XXXXXX.yaml")
          trap 'rm -f "$TMP_YAML"' EXIT
          cat > "$TMP_YAML" <<EOF
### model
model_name_or_path: ${BASE_PATH_LF_REL[$VARIANT]}
adapter_name_or_path: ${ADAPTER_REL}
trust_remote_code: true
finetuning_type: lora
template: ${MODEL_TEMPLATE[$MODEL_SHORT]}

### export
export_dir: ${MERGED_REL}
export_size: 5
export_device: cpu
export_legacy_format: false
export_hub_model_id: ${HUB_ID}
EOF

          run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
            bash -c "cd '$LF_ROOT' && llamafactory-cli export '$TMP_YAML'"

          rm -f "$TMP_YAML"
          trap - EXIT
          ;;

        unsloth)
          run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
            python "$BASE_DIR/scripts/_unsloth_merge.py" \
              --mode lora \
              --base-model "${BASE_PATH[$VARIANT]}" \
              --checkpoint "$ADAPTER_ABS" \
              --export-dir "$LOCAL_DIR" \
              --hub-id "$HUB_ID"
          ;;

        *)
          echo "[!] Unknown backend '$BACKEND' for model $MODEL_SHORT" >&2
          exit 2
          ;;
      esac

      if [ ! -d "$LOCAL_DIR" ]; then
        echo "[!] [$MODEL_SHORT][$DS][$VARIANT] Expected output dir missing: $LOCAL_DIR" >&2
        exit 1
      fi
      echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Stage 2 merged model: $LOCAL_DIR" >&2
      MERGED_COUNT=$((MERGED_COUNT + 1))
    done
  done
done

echo "--- Stage 2 Merge: $MERGED_COUNT merged, $SKIPPED_COUNT skipped ---" >&2
if [ "$MERGED_COUNT" -eq 0 ] && [ "$SKIPPED_COUNT" -gt 0 ]; then
  echo "[!] No variants were merged. Run stage2 evaluation first." >&2
  exit 1
fi
