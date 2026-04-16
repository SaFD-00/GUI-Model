#!/usr/bin/env bash
# Stage 2 Merge & Upload — BEST_CHECKPOINT 기반 winner adapter 자동 선택
#
# 두 variant 각각:
#   merge_base          - Base model          + lora_base/<winner>
#   merge_world_model   - stage1_merged       + lora_world_model/<winner>
#
# 선택 근거:
#   saves/{MODEL}/{DS}/stage2_lora/{lora_base,lora_world_model}/BEST_CHECKPOINT
#   (stage2_eval.sh 또는 notebook Section 7 evaluation cell 이 기록)
#
# BEST_CHECKPOINT 없으면 해당 variant 를 [SKIP] 경고 후 건너뜀.
# HF push 는 merge YAML 의 export_hub_model_id 필드 기준.
# YAML export_dir 이 outputs/{MODEL}/{DS}/stage2_merged/{base,world_model}/ 를 직접 가리킴.
#
# 전제: .env 의 HF_TOKEN. stage1_merge.sh 가 outputs/{MODEL}/{DS}/stage1_merged/ 를 생성해둔 상태.

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_merge"
MERGED_COUNT=0
SKIPPED_COUNT=0

for MODEL_SHORT in "${MODELS[@]}"; do
  BASE_MODEL="${MODEL_ID[$MODEL_SHORT]}"

  for DS in "${DATASETS[@]}"; do
    STAGE1_MERGED_REL="outputs/${MODEL_SHORT}/${DS}/stage1_merged"
    STAGE1_MERGED="$LF_ROOT/$STAGE1_MERGED_REL"

    if [ ! -d "$STAGE1_MERGED" ]; then
      echo "[SKIP] [$MODEL_SHORT][$DS] Missing $STAGE1_MERGED — stage1_merge.sh 미완료, 건너뜁니다." >&2
      SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
      continue
    fi

    # variant → (base model path, lora output dir, local output subdir)
    declare -A BASE_PATH=(
      [merge_base]="$BASE_MODEL"
      [merge_world_model]="./${STAGE1_MERGED_REL}"
    )
    declare -A LORA_DIR_REL=(
      [merge_base]="saves/${MODEL_SHORT}/${DS}/stage2_lora/lora_base"
      [merge_world_model]="saves/${MODEL_SHORT}/${DS}/stage2_lora/lora_world_model"
    )
    declare -A LOCAL_VARIANT_DIR=(
      [merge_base]="base"
      [merge_world_model]="world_model"
    )

    for VARIANT in merge_base merge_world_model; do
      LORA_REL="${LORA_DIR_REL[$VARIANT]}"
      BEST_FILE="$LF_ROOT/$LORA_REL/BEST_CHECKPOINT"

      # 1. BEST_CHECKPOINT 존재 확인 (없으면 variant skip)
      if [ ! -f "$BEST_FILE" ]; then
        echo "[SKIP] [$MODEL_SHORT][$DS][$VARIANT] BEST_CHECKPOINT not found at $BEST_FILE — stage2 평가 미완료, 건너뜁니다." >&2
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
        continue
      fi

      # 2. BEST_CHECKPOINT → ADAPTER_REL 결정
      CKPT_NAME=$(tr -d '[:space:]' < "$BEST_FILE")
      ADAPTER_REL="./${LORA_REL}/${CKPT_NAME}"
      echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Using Stage 2 winner: ${CKPT_NAME}" >&2

      # 3. merge YAML 자동 생성 (노트북 Cell 140 과 동일한 형식)
      TMP_YAML=$(mktemp -t "stage2_merge_${MODEL_SHORT}_${DS}_${VARIANT}_XXXXXX.yaml")
      trap 'rm -f "$TMP_YAML"' EXIT
      cat > "$TMP_YAML" <<EOF
### model
model_name_or_path: ${BASE_PATH[$VARIANT]}
adapter_name_or_path: ${ADAPTER_REL}
trust_remote_code: true
finetuning_type: lora
template: ${MODEL_TEMPLATE[$MODEL_SHORT]}

### export
export_dir: ./outputs/${MODEL_SHORT}/${DS}/stage2_merged/${LOCAL_VARIANT_DIR[$VARIANT]}
export_size: 5
export_device: cpu
export_legacy_format: false
export_hub_model_id: SaFD-00/${MODEL_SHORT}-${HF_SLUG[$DS]}stage2-${LOCAL_VARIANT_DIR[$VARIANT]//_/-}
EOF

      # 4. HF push + 로컬 저장 (llamafactory-cli export)
      #    YAML export_dir 이 outputs/{MODEL}/{DS}/stage2_merged/{base,world_model}/ 를 직접 가리킴
      run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
        bash -c "cd '$LF_ROOT' && llamafactory-cli export '$TMP_YAML'"

      # 검증: export_dir 결과물 존재 확인
      LOCAL_DIR="$LF_ROOT/outputs/${MODEL_SHORT}/${DS}/stage2_merged/${LOCAL_VARIANT_DIR[$VARIANT]}"
      if [ ! -d "$LOCAL_DIR" ]; then
        echo "[!] [$MODEL_SHORT][$DS][$VARIANT] Expected output dir missing: $LOCAL_DIR" >&2
        echo "    Check merge YAML export_dir field." >&2
        exit 1
      fi
      echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Stage 2 merged model: $LOCAL_DIR" >&2
      MERGED_COUNT=$((MERGED_COUNT + 1))

      rm -f "$TMP_YAML"
      trap - EXIT
    done
  done
done

echo "--- Stage 2 Merge: $MERGED_COUNT merged, $SKIPPED_COUNT skipped ---" >&2
if [ "$MERGED_COUNT" -eq 0 ] && [ "$SKIPPED_COUNT" -gt 0 ]; then
  echo "[!] No variants were merged. Run stage2 evaluation first." >&2
  exit 1
fi
