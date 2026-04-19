#!/usr/bin/env bash
# Stage 2 Merge — 전체 epoch adapter 를 각각 merge + HF Hub push.
#
# train → merge → eval 흐름 전환: BEST_CHECKPOINT 의존 제거. 모든
# outputs/{DS}/adapters/{MODEL}_stage2_lora_{base|world_model_from_{MODE}}/checkpoint-*/ 를
# 순회하며 epoch 별로 local merge + 개별 HF repo push 한다.
#
# --stage1-mode full (default) | lora 에 따라 world-model variant 상류를 선택:
#   merge_base                              - Base model + lora_base adapter
#   merge_world_model_from_${MODE}          - S1 winner merged + lora_world_model_from_${MODE} adapter
#     (S1 winner 는 adapters/{M}_stage1_{MODE}/BEST_CHECKPOINT.json 의 epoch 필드로 결정)
#
# Backend 분기 (_common.sh::MODEL_BACKEND):
#   - llamafactory: 임시 merge YAML 생성 → llamafactory-cli export
#   - unsloth:      scripts/_unsloth_merge.py --mode lora
#
# HF repo id 규칙 (단일 정의: _common.sh::hf_repo_id_stage2):
#   SaFD-00/{short}-{slug}stage2-{base|{MODE}-world-model}-epoch{E}
#
# 로컬 산출물 (사용자 정책: 전부 보존):
#   outputs/{DS}/merged/{MODEL}_stage2_lora_{base|world_model_from_{MODE}}/epoch-{E}/
#
# 요구: HF_TOKEN (.env 또는 환경변수)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_merge_from_${STAGE1_MODE}"
MERGED_COUNT=0
FAILED_COUNT=0
SKIPPED_COUNT=0

# world-model variant: BEST_CHECKPOINT.json → Ewin → local merged path 반환 (없으면 hard-fail)
resolve_stage1_winner_base() {
  local model_short="$1" ds="$2" mode="$3"
  local best_json="$BASE_DIR/outputs/${ds}/adapters/${model_short}_stage1_${mode}/BEST_CHECKPOINT.json"
  if [ ! -f "$best_json" ]; then
    return 1
  fi
  python - "$best_json" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
e = d.get("epoch")
if e is None:
    sys.stderr.write("[!] BEST_CHECKPOINT.json has no 'epoch' field\n")
    sys.exit(1)
print(int(e))
PY
}

for MODEL_SHORT in "${MODELS[@]}"; do
  BASE_MODEL="${MODEL_ID[$MODEL_SHORT]}"
  BACKEND="$(get_backend "$MODEL_SHORT")"

  for DS in "${DATASETS[@]}"; do
    # Stage 1 winner 로컬 merged 경로 (world-model variant 에만 해당)
    S1_WINNER_EPOCH=""
    if WIN_EPOCH=$(resolve_stage1_winner_base "$MODEL_SHORT" "$DS" "$STAGE1_MODE"); then
      S1_WINNER_EPOCH="$WIN_EPOCH"
      S1_WINNER_ABS="$(local_merged_epoch_dir stage1 "$MODEL_SHORT" "$DS" "$STAGE1_MODE" "$S1_WINNER_EPOCH")"
      S1_WINNER_REL="../outputs/${DS}/merged/${MODEL_SHORT}_stage1_${STAGE1_MODE}/epoch-${S1_WINNER_EPOCH}"
    else
      S1_WINNER_ABS=""
      S1_WINNER_REL=""
    fi
    if [ -z "$S1_WINNER_EPOCH" ] || [ ! -d "$S1_WINNER_ABS" ]; then
      echo "[WARN] [$MODEL_SHORT][$DS] Missing Stage 1 winner merged dir — world-model-${STAGE1_MODE} merge 건너뜀. (base variant 는 계속 진행)" >&2
      echo "       expected: $S1_WINNER_ABS (from BEST_CHECKPOINT.json.epoch)" >&2
      S1_WINNER_AVAILABLE=0
    else
      S1_WINNER_AVAILABLE=1
    fi

    WORLD_ADAPTER_SUB="${MODEL_SHORT}_stage2_lora_world_model_from_${STAGE1_MODE}"

    # variant key → (base, base_LF_rel, adapter_dir, merged_subdir, hub_suffix)
    declare -A VARIANT_BASE_ABS=(
      [merge_base]="$BASE_MODEL"
      [merge_world_model]="${S1_WINNER_ABS}"
    )
    declare -A VARIANT_BASE_LF_REL=(
      [merge_base]="$BASE_MODEL"
      [merge_world_model]="${S1_WINNER_REL}"
    )
    declare -A VARIANT_ADAPTER_DIR=(
      [merge_base]="${MODEL_SHORT}_stage2_lora_base"
      [merge_world_model]="${WORLD_ADAPTER_SUB}"
    )
    declare -A VARIANT_HUB_SUFFIX=(
      [merge_base]="base"
      [merge_world_model]="${STAGE1_MODE}-world-model"
    )

    for VARIANT in merge_base merge_world_model; do
      if [ "$VARIANT" = "merge_world_model" ] && [ "$S1_WINNER_AVAILABLE" -eq 0 ]; then
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
        continue
      fi

      ADAPTER_SUB="${VARIANT_ADAPTER_DIR[$VARIANT]}"
      TRAIN_DIR="$BASE_DIR/outputs/${DS}/adapters/${ADAPTER_SUB}"
      TRAIN_DIR_REL="../outputs/${DS}/adapters/${ADAPTER_SUB}"

      shopt -s nullglob
      CKPTS=("$TRAIN_DIR"/checkpoint-*/)
      shopt -u nullglob
      if [ "${#CKPTS[@]}" -eq 0 ]; then
        echo "[!] [$MODEL_SHORT][$DS][$VARIANT] No checkpoints under $TRAIN_DIR — run stage2_train.sh first." >&2
        exit 1
      fi
      echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Merging ${#CKPTS[@]} checkpoints" >&2

      for CKPT_DIR in "${CKPTS[@]}"; do
        CKPT_DIR="${CKPT_DIR%/}"
        CKPT_NAME=$(basename "$CKPT_DIR")
        EPOCH=$(ckpt_epoch_from_dir "$CKPT_DIR") || {
          echo "[!] [$MODEL_SHORT][$DS][$VARIANT][$CKPT_NAME] epoch 파싱 실패" >&2
          FAILED_COUNT=$((FAILED_COUNT + 1)); continue
        }

        VARIANT_KEY="$ADAPTER_SUB"
        VARIANT_KEY="${VARIANT_KEY#${MODEL_SHORT}_stage2_}"    # e.g. lora_base, lora_world_model_from_full
        HUB_ID=$(hf_repo_id_stage2 "$MODEL_SHORT" "$DS" "${VARIANT_HUB_SUFFIX[$VARIANT]}" "$EPOCH")
        MERGED_REL="../outputs/${DS}/merged/${MODEL_SHORT}_stage2_${VARIANT_KEY}/epoch-${EPOCH}"
        LOCAL_DIR="$(local_merged_epoch_dir stage2 "$MODEL_SHORT" "$DS" "$VARIANT_KEY" "$EPOCH")"
        ADAPTER_REL="${TRAIN_DIR_REL}/${CKPT_NAME}"
        ADAPTER_ABS="$TRAIN_DIR/$CKPT_NAME"

        echo "[+] [$MODEL_SHORT][$DS][$VARIANT] ${CKPT_NAME} (epoch=${EPOCH}) → ${HUB_ID}" >&2

        case "$BACKEND" in
          llamafactory)
            TMP_YAML=$(mktemp -t "stage2_merge_${MODEL_SHORT}_${DS}_${VARIANT}_ep${EPOCH}_XXXXXX.yaml")
            cat > "$TMP_YAML" <<EOF
### model
model_name_or_path: ${VARIANT_BASE_LF_REL[$VARIANT]}
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
            if ! run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}_epoch${EPOCH}" \
              bash -c "cd '$LF_ROOT' && llamafactory-cli export '$TMP_YAML'"; then
              FAILED_COUNT=$((FAILED_COUNT + 1))
              rm -f "$TMP_YAML"
              continue
            fi
            rm -f "$TMP_YAML"
            ;;

          unsloth)
            run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}_epoch${EPOCH}" \
              python "$BASE_DIR/scripts/_unsloth_merge.py" \
                --mode lora \
                --base-model "${VARIANT_BASE_ABS[$VARIANT]}" \
                --checkpoint "$ADAPTER_ABS" \
                --export-dir "$LOCAL_DIR" \
                --hub-id "$HUB_ID" \
              || { FAILED_COUNT=$((FAILED_COUNT + 1)); continue; }
            ;;

          *)
            echo "[!] Unknown backend '$BACKEND' for model $MODEL_SHORT" >&2
            exit 2
            ;;
        esac

        if [ ! -d "$LOCAL_DIR" ]; then
          echo "[!] [$MODEL_SHORT][$DS][$VARIANT][epoch${EPOCH}] Expected output dir missing: $LOCAL_DIR" >&2
          FAILED_COUNT=$((FAILED_COUNT + 1))
          continue
        fi
        MERGED_COUNT=$((MERGED_COUNT + 1))
      done
    done

    unset VARIANT_BASE_ABS VARIANT_BASE_LF_REL VARIANT_ADAPTER_DIR VARIANT_HUB_SUFFIX
  done
done

echo "--- Stage 2 Merge (from ${STAGE1_MODE}): $MERGED_COUNT merged, $SKIPPED_COUNT skipped, $FAILED_COUNT failed ---" >&2
if [ "$FAILED_COUNT" -gt 0 ]; then
  echo "[!] Some epochs failed. Re-run after fixing." >&2
  exit 1
fi
if [ "$MERGED_COUNT" -eq 0 ]; then
  echo "[!] No variants were merged." >&2
  exit 1
fi
