#!/usr/bin/env bash
# Stage 1 Merge — BEST_CHECKPOINT 기반 자동 선택 + HF Hub push + 로컬 복사
#
#   1. outputs/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT 읽기
#      (없으면 load_best_model_at_end 루트로 fallback, WARN)
#   2. merge YAML (examples/merge_custom/GUI-Model-{DS}/gui/qwen3_vl_8b_gui.yaml) 의
#      model_name_or_path 를 런타임 override 한 임시 YAML 생성
#   3. llamafactory-cli export → HF Hub push + exports/ 에 가중치 저장
#   4. exports/qwen3-vl-8b-<slug>stage1-world-model/ 을
#      outputs/{DS}/stage1_merged/ 로 rsync (로컬 복사)
#
# 요구: HF_TOKEN (.env 또는 환경변수), rsync, pyyaml

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_dataset_arg "${1:-all}"

SCRIPT_TAG="stage1_merge"

for DS in "${DATASETS[@]}"; do
  SLUG="${HF_SLUG[$DS]}"
  TRAIN_DIR_REL="outputs/${DS}/stage1_full/full_world_model"
  TRAIN_DIR="$LF_ROOT/$TRAIN_DIR_REL"
  BEST_FILE="$TRAIN_DIR/BEST_CHECKPOINT"
  ORIG_YAML="$LF_ROOT/examples/merge_custom/GUI-Model-${DS}/gui/qwen3_vl_8b_gui.yaml"
  require_yaml "examples/merge_custom/GUI-Model-${DS}/gui/qwen3_vl_8b_gui.yaml" \
    "run notebook Cell 17 to generate this YAML"

  # 1. BEST_CHECKPOINT → MODEL_REL 결정 (cwd=LF_ROOT 기준)
  if [ -f "$BEST_FILE" ]; then
    CKPT_NAME=$(tr -d '[:space:]' < "$BEST_FILE")
    MODEL_REL="./${TRAIN_DIR_REL}/${CKPT_NAME}"
    echo "[+] [$DS] Using Hungarian F1 winner: ${CKPT_NAME}" >&2
  else
    MODEL_REL="./${TRAIN_DIR_REL}"
    echo "[!] [$DS] BEST_CHECKPOINT not found — fallback to load_best_model_at_end root ($MODEL_REL)" >&2
  fi

  # 2. 임시 YAML 렌더 (원본은 보존)
  TMP_YAML=$(mktemp -t "stage1_merge_${DS}_XXXXXX.yaml")
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

  # 3. HF push (llamafactory-cli export)
  run_logged "${SCRIPT_TAG}_${DS}_hf" \
    bash -c "cd '$LF_ROOT' && llamafactory-cli export '$TMP_YAML'"

  # 4. 로컬 복사 (exports/ → outputs/{DS}/stage1_merged/)
  EXPORT_DIR="$LF_ROOT/exports/qwen3-vl-8b-${SLUG}stage1-world-model"
  LOCAL_DIR="$LF_ROOT/outputs/${DS}/stage1_merged"
  if [ ! -d "$EXPORT_DIR" ]; then
    echo "[!] [$DS] Expected export dir missing: $EXPORT_DIR" >&2
    echo "    Check merge YAML export_dir / export_hub_model_id fields." >&2
    exit 1
  fi
  run_logged "${SCRIPT_TAG}_${DS}_local" \
    rsync -a --delete "$EXPORT_DIR/" "$LOCAL_DIR/"

  rm -f "$TMP_YAML"
  trap - EXIT
done
