#!/usr/bin/env bash
# Stage 2 Evaluation (base only) — Zero-shot baseline 단일 실행.
#
# stage2_eval.sh 의 Phase A 만 떼어낸 경량 스크립트. lora 변형 (lora_base /
# lora_world_model_from_*) 는 전혀 건드리지 않는다.
#
# 대상: MODEL_ID[$MODEL_SHORT] (HF Hub 의 원본 base 모델)
# 산출물: outputs/{DS}/eval/{MODEL}/stage2_eval/base/
#           ├ generated_predictions.jsonl
#           ├ predict_results.json
#           └ action_metrics.json
#
# 사용:
#   bash ./scripts/stage2_eval_base.sh --model qwen2.5-vl-7b --dataset AC

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_eval_base"

declare -A DS_DATADIR=( [MB]="MobiBench" [AC]="AndroidControl" )

for MODEL_SHORT in "${MODELS[@]}"; do
  BASE_MODEL="${MODEL_ID[$MODEL_SHORT]}"
  TEMPLATE="${MODEL_TEMPLATE[$MODEL_SHORT]}"

  VLLM_COMMON_ARGS=(
    --template "$TEMPLATE"
    --cutoff_len 10240
    --image_max_pixels 4233600
  )
  if [[ "$TEMPLATE" == qwen3_vl* ]]; then
    VLLM_COMMON_ARGS+=(--enable_thinking False)
  fi

  for DS in "${DATASETS[@]}"; do
    PREFIX="${DS_PREFIX[$DS]}"
    DS_TEST="${PREFIX}_stage2_test"
    TEST_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage2_test.jsonl"
    S2_EVAL_OUT_REL="../outputs/${DS}/eval/${MODEL_SHORT}/stage2_eval"
    OUT_BASE_REL="${S2_EVAL_OUT_REL}/base"
    OUT_BASE="$LF_ROOT/$OUT_BASE_REL"

    if [ ! -f "$TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test file: $TEST_JSONL" >&2
      exit 1
    fi

    BASE_TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}"
    if skip_if_done "$BASE_TAG" "$OUT_BASE/action_metrics.json"; then
      continue
    fi
    run_logged "$BASE_TAG" \
      bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_BASE_REL' && \
        python scripts/vllm_infer.py \
          --model_name_or_path '$BASE_MODEL' \
          --dataset '$DS_TEST' \
          --dataset_dir '$LF_ROOT/data' \
          ${VLLM_COMMON_ARGS[*]} \
          --vllm_config '{\"gpu_memory_utilization\": 0.80}' \
          --save_name        '$OUT_BASE_REL/generated_predictions.jsonl' \
          --matrix_save_name '$OUT_BASE_REL/predict_results.json' && \
        python '$BASE_DIR/scripts/_action_eval.py' score \
          --test   '$TEST_JSONL' \
          --pred   '$OUT_BASE/generated_predictions.jsonl' \
          --output '$OUT_BASE/action_metrics.json'"
  done
done
