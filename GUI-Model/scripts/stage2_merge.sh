#!/usr/bin/env bash
# Stage 2 Merge & Upload — BEST_CHECKPOINT 기반 winner adapter 자동 선택
#
# 두 variant 각각:
#   merge_base          - Qwen/Qwen3-VL-8B-Instruct          + lora_base/<winner>
#   merge_world_model   - outputs/{DS}/stage1_merged          + lora_world_model/<winner>
#
# 선택 근거:
#   saves/{DS}/stage2_lora/{lora_base,lora_world_model}/BEST_CHECKPOINT
#   (stage2_eval.sh 또는 notebook Section 7 evaluation cell 이 기록)
#
# BEST_CHECKPOINT 없으면 hard-fail — stage2_eval.sh 먼저 실행 필요.
# HF push 는 merge YAML 의 export_hub_model_id 필드 기준.
# HF push 후 exports/ → outputs/{DS}/stage2_merged/{base,world_model}/ 로 rsync (Stage 1 과 동일 패턴).
#
# 전제: .env 의 HF_TOKEN, rsync, pyyaml. stage1_merge.sh 가 outputs/{DS}/stage1_merged/ 를 생성해둔 상태.

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_dataset_arg "${1:-all}"

SCRIPT_TAG="stage2_merge"

for DS in "${DATASETS[@]}"; do
  SLUG="${HF_SLUG[$DS]}"
  STAGE1_MERGED_REL="outputs/${DS}/stage1_merged"
  STAGE1_MERGED="$LF_ROOT/$STAGE1_MERGED_REL"

  if [ ! -d "$STAGE1_MERGED" ]; then
    echo "[!] [$DS] Missing $STAGE1_MERGED — run stage1_merge.sh first." >&2
    exit 1
  fi

  # variant → (merge YAML 파일명, base model path, lora output dir, export dir 이름)
  declare -A BASE_PATH=(
    [merge_base]="Qwen/Qwen3-VL-8B-Instruct"
    [merge_world_model]="./${STAGE1_MERGED_REL}"
  )
  declare -A LORA_DIR_REL=(
    [merge_base]="saves/${DS}/stage2_lora/lora_base"
    [merge_world_model]="saves/${DS}/stage2_lora/lora_world_model"
  )
  # variant → (exports/ 하위 디렉토리 이름, outputs/ 하위 로컬 복사 디렉토리 이름)
  declare -A EXPORT_SUFFIX=(
    [merge_base]="stage2-base"
    [merge_world_model]="stage2-world-model"
  )
  declare -A LOCAL_VARIANT_DIR=(
    [merge_base]="base"
    [merge_world_model]="world_model"
  )

  for VARIANT in merge_base merge_world_model; do
    ORIG_YAML="$LF_ROOT/examples/merge_custom/GUI-Model-${DS}/stage2/${VARIANT}.yaml"
    require_yaml "examples/merge_custom/GUI-Model-${DS}/stage2/${VARIANT}.yaml" \
      "run notebook Cell 16 (after stage2_eval.sh generates BEST_CHECKPOINT) to generate this YAML"

    LORA_REL="${LORA_DIR_REL[$VARIANT]}"
    BEST_FILE="$LF_ROOT/$LORA_REL/BEST_CHECKPOINT"

    # 1. BEST_CHECKPOINT → ADAPTER_REL 결정
    if [ ! -f "$BEST_FILE" ]; then
      echo "[!] [$DS][$VARIANT] BEST_CHECKPOINT not found at $BEST_FILE" >&2
      echo "    Run 'bash scripts/stage2_eval.sh $DS' first to select the Overall Score winner." >&2
      exit 1
    fi
    CKPT_NAME=$(tr -d '[:space:]' < "$BEST_FILE")
    ADAPTER_REL="./${LORA_REL}/${CKPT_NAME}"
    echo "[+] [$DS][$VARIANT] Using Stage 2 winner: ${CKPT_NAME}" >&2

    # 2. 임시 YAML 렌더 (model_name_or_path + adapter_name_or_path override)
    TMP_YAML=$(mktemp -t "stage2_merge_${DS}_${VARIANT}_XXXXXX.yaml")
    trap 'rm -f "$TMP_YAML"' EXIT
    python3 - "$ORIG_YAML" "${BASE_PATH[$VARIANT]}" "$ADAPTER_REL" "$TMP_YAML" <<'PY'
import sys, yaml
src, model_path, adapter_path, dst = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(src, 'r', encoding='utf-8') as f:
    y = yaml.safe_load(f)
y['model_name_or_path'] = model_path
y['adapter_name_or_path'] = adapter_path
with open(dst, 'w', encoding='utf-8') as f:
    yaml.safe_dump(y, f, allow_unicode=True, sort_keys=False)
PY

    # 3. HF push (llamafactory-cli export)
    run_logged "${SCRIPT_TAG}_${DS}_${VARIANT}_hf" \
      bash -c "cd '$LF_ROOT' && llamafactory-cli export '$TMP_YAML'"

    # 4. 로컬 복사 (exports/ → outputs/{DS}/stage2_merged/{base,world_model}/)
    EXPORT_DIR="$LF_ROOT/exports/qwen3-vl-8b-${SLUG}${EXPORT_SUFFIX[$VARIANT]}"
    LOCAL_DIR="$LF_ROOT/outputs/${DS}/stage2_merged/${LOCAL_VARIANT_DIR[$VARIANT]}"
    if [ ! -d "$EXPORT_DIR" ]; then
      echo "[!] [$DS][$VARIANT] Expected export dir missing: $EXPORT_DIR" >&2
      echo "    Check merge YAML export_dir / export_hub_model_id fields." >&2
      exit 1
    fi
    mkdir -p "$(dirname "$LOCAL_DIR")"
    run_logged "${SCRIPT_TAG}_${DS}_${VARIANT}_local" \
      rsync -a --delete "$EXPORT_DIR/" "$LOCAL_DIR/"

    rm -f "$TMP_YAML"
    trap - EXIT
  done
done
