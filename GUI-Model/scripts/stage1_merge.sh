#!/usr/bin/env bash
# Stage 1 Merge — BEST_CHECKPOINT 기반 자동 선택 + HF Hub push
#
#   1. saves/{MODEL}/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT 읽기
#      (없으면 [SKIP] 경고 후 건너뜀 — stage1_eval.sh 먼저 실행 필요)
#   2. merge YAML (examples/merge_custom/GUI-Model-{DS}/gui/{MODEL}.yaml) 의
#      model_name_or_path 를 런타임 override 한 임시 YAML 생성
#   3. llamafactory-cli export → HF Hub push + outputs/{MODEL}/{DS}/stage1_merged/ 에 가중치 저장
#
# 요구: HF_TOKEN (.env 또는 환경변수), pyyaml

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"

SCRIPT_TAG="stage1_merge"
MERGED_COUNT=0
SKIPPED_COUNT=0

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    TRAIN_DIR_REL="saves/${MODEL_SHORT}/${DS}/stage1_full/full_world_model"
    TRAIN_DIR="$LF_ROOT/$TRAIN_DIR_REL"
    BEST_FILE="$TRAIN_DIR/BEST_CHECKPOINT"
    ORIG_YAML="$LF_ROOT/examples/merge_custom/GUI-Model-${DS}/gui/${MODEL_SHORT}.yaml"

    # 1. BEST_CHECKPOINT 존재 확인 (없으면 skip)
    if [ ! -f "$BEST_FILE" ]; then
      echo "[SKIP] [$MODEL_SHORT][$DS] BEST_CHECKPOINT not found at $BEST_FILE — stage1 평가 미완료, 건너뜁니다." >&2
      SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
      continue
    fi

    # 2. Merge YAML 존재 확인 (없으면 skip)
    if [ ! -f "$ORIG_YAML" ]; then
      echo "[SKIP] [$MODEL_SHORT][$DS] Merge YAML not found at $ORIG_YAML — notebook Cell 12 재실행 필요, 건너뜁니다." >&2
      SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
      continue
    fi

    # 3. BEST_CHECKPOINT → MODEL_REL 결정 (cwd=LF_ROOT 기준)
    CKPT_NAME=$(tr -d '[:space:]' < "$BEST_FILE")
    MODEL_REL="./${TRAIN_DIR_REL}/${CKPT_NAME}"
    echo "[+] [$MODEL_SHORT][$DS] Using Hungarian F1 winner: ${CKPT_NAME}" >&2

    # 4. 임시 YAML 렌더 (원본은 보존)
    TMP_YAML=$(mktemp -t "stage1_merge_${MODEL_SHORT}_${DS}_XXXXXX.yaml")
    trap 'rm -f "$TMP_YAML"' EXIT
    python3 - "$ORIG_YAML" "$MODEL_REL" "$TMP_YAML" <<'PY'
import sys, yaml
src, model_path, dst = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, 'r', encoding='utf-8') as f:
    y = yaml.safe_load(f)
y['model_name_or_path'] = model_path
with open(dst, 'w', encoding='utf-8') as f:
    yaml.safe_dump(y, f, allow_unicode=True, sort_keys=False)
PY

    # 5. HF push + 로컬 저장 (llamafactory-cli export)
    #    YAML export_dir 이 outputs/{MODEL}/{DS}/stage1_merged/ 를 직접 가리킴
    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}" \
      bash -c "cd '$LF_ROOT' && llamafactory-cli export '$TMP_YAML'"

    # 검증: export_dir 결과물 존재 확인
    LOCAL_DIR="$LF_ROOT/outputs/${MODEL_SHORT}/${DS}/stage1_merged"
    if [ ! -d "$LOCAL_DIR" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Expected output dir missing: $LOCAL_DIR" >&2
      echo "    Check merge YAML export_dir field." >&2
      exit 1
    fi
    echo "[+] [$MODEL_SHORT][$DS] Stage 1 merged model: $LOCAL_DIR" >&2
    MERGED_COUNT=$((MERGED_COUNT + 1))

    rm -f "$TMP_YAML"
    trap - EXIT
  done
done

echo "--- Stage 1 Merge: $MERGED_COUNT merged, $SKIPPED_COUNT skipped ---" >&2
if [ "$MERGED_COUNT" -eq 0 ] && [ "$SKIPPED_COUNT" -gt 0 ]; then
  echo "[!] No models were merged. Run stage1 evaluation first." >&2
  exit 1
fi
