#!/usr/bin/env bash
# Stage 1 Merge & HuggingFace Hub Upload (gui-model.ipynb Cell 18)
# - outputs/<DS>/stage1_full/full_world_model 체크포인트 → HF Hub push
# - 요구: HF_TOKEN 환경변수 또는 `.env` 에 설정

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_dataset_arg "${1:-all}"

SCRIPT_TAG="stage1_merge"

for DS in "${DATASETS[@]}"; do
  YAML="examples/merge_custom/GUI-Model-${DS}/gui/qwen3_vl_8b_gui.yaml"
  require_yaml "$YAML" "run notebook Cell 17 to generate this YAML"

  run_logged "${SCRIPT_TAG}_${DS}" \
    bash -c "cd '$LF_ROOT' && llamafactory-cli export '$YAML'"
done
