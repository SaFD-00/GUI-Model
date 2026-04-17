#!/usr/bin/env bash
# Stage 1 Merge — BEST_CHECKPOINT 기반 자동 선택 + HF Hub push
#
# Backend 분기 (_common.sh::MODEL_BACKEND):
#   - llamafactory: 임시 merge YAML 생성 → llamafactory-cli export
#   - unsloth:      scripts/_unsloth_merge.py --mode full (full FT 체크포인트 copy+push)
#
# 요구: HF_TOKEN (.env 또는 환경변수)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage1_merge"
MERGED_COUNT=0
SKIPPED_COUNT=0

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    BACKEND="$(get_backend "$MODEL_SHORT")"
    # LF cwd 기준 상대경로 (= BASE_DIR 기준 "outputs/...").
    TRAIN_DIR_REL="../outputs/${DS}/adapters/${MODEL_SHORT}_stage1_full"
    TRAIN_DIR="$LF_ROOT/$TRAIN_DIR_REL"
    BEST_FILE="$TRAIN_DIR/BEST_CHECKPOINT"

    if [ ! -f "$BEST_FILE" ]; then
      echo "[SKIP] [$MODEL_SHORT][$DS] BEST_CHECKPOINT not found at $BEST_FILE — stage1 평가 미완료, 건너뜁니다." >&2
      SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
      continue
    fi

    CKPT_NAME=$(tr -d '[:space:]' < "$BEST_FILE")
    MODEL_REL="./${TRAIN_DIR_REL}/${CKPT_NAME}"
    echo "[+] [$MODEL_SHORT][$DS] Using Hungarian F1 winner: ${CKPT_NAME}" >&2

    HUB_ID="SaFD-00/${MODEL_SHORT}-${HF_SLUG[$DS]}stage1-world-model"
    MERGED_REL="../outputs/${DS}/merged/${MODEL_SHORT}_stage1_full"
    LOCAL_DIR="$BASE_DIR/outputs/${DS}/merged/${MODEL_SHORT}_stage1_full"

    case "$BACKEND" in
      llamafactory)
        TMP_YAML=$(mktemp -t "stage1_merge_${MODEL_SHORT}_${DS}_XXXXXX.yaml")
        trap 'rm -f "$TMP_YAML"' EXIT
        cat > "$TMP_YAML" <<EOF
### model
model_name_or_path: ${MODEL_REL}
trust_remote_code: true
template: ${MODEL_TEMPLATE[$MODEL_SHORT]}

### export
export_dir: ${MERGED_REL}
export_size: 5
export_device: cpu
export_legacy_format: false
export_hub_model_id: ${HUB_ID}
EOF

        run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}" \
          bash -c "cd '$LF_ROOT' && llamafactory-cli export '$TMP_YAML'"

        rm -f "$TMP_YAML"
        trap - EXIT
        ;;

      unsloth)
        CKPT_ABS="$TRAIN_DIR/${CKPT_NAME}"
        run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}" \
          python "$BASE_DIR/scripts/_unsloth_merge.py" \
            --mode full \
            --checkpoint "$CKPT_ABS" \
            --export-dir "$LOCAL_DIR" \
            --hub-id "$HUB_ID"
        ;;

      *)
        echo "[!] Unknown backend '$BACKEND' for model $MODEL_SHORT" >&2
        exit 2
        ;;
    esac

    if [ ! -d "$LOCAL_DIR" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Expected output dir missing: $LOCAL_DIR" >&2
      exit 1
    fi
    echo "[+] [$MODEL_SHORT][$DS] Stage 1 merged model: $LOCAL_DIR" >&2
    MERGED_COUNT=$((MERGED_COUNT + 1))
  done
done

echo "--- Stage 1 Merge: $MERGED_COUNT merged, $SKIPPED_COUNT skipped ---" >&2
if [ "$MERGED_COUNT" -eq 0 ] && [ "$SKIPPED_COUNT" -gt 0 ]; then
  echo "[!] No models were merged. Run stage1 evaluation first." >&2
  exit 1
fi
