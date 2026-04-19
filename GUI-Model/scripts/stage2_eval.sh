#!/usr/bin/env bash
# Stage 2 Evaluation Pipeline — HF Hub 업로드된 merged 모델 평가
#
# --stage1-mode full (default) | lora 에 따라 world-model variant 의 Hub ID 결정:
#   full → SaFD-00/{short}-{slug}stage2-full-world-model
#   lora → SaFD-00/{short}-{slug}stage2-lora-world-model
#
# 3-Way:
#   base                           - Zero-shot baseline ($BASE_MODEL)
#   lora_base                      - HF: SaFD-00/{short}-{slug}stage2-base
#   lora_world_model_${MODE}       - HF: SaFD-00/{short}-{slug}stage2-${MODE}-world-model
#
# HF 업로드본은 stage2_merge.sh 에서 winner checkpoint + base 를 merge 한 단일 모델이므로
# 로컬 adapter 로딩 / checkpoint sweep / winner 선택 단계는 불필요하다.
#
# 산출물 (BASE_DIR 기준):
#   outputs/{DS}/eval/{MODEL}/stage2_eval/{base|lora_base|lora_world_model_${MODE}}/
#     (generated_predictions.jsonl | predict_results.json | action_metrics.json)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_eval_from_${STAGE1_MODE}"

declare -A DS_DATADIR=( [MB]="MobiBench" [AC]="AndroidControl" )

for MODEL_SHORT in "${MODELS[@]}"; do
  BASE_MODEL="${MODEL_ID[$MODEL_SHORT]}"
  TEMPLATE="${MODEL_TEMPLATE[$MODEL_SHORT]}"

  VLLM_COMMON_ARGS=(
    --template "$TEMPLATE"
    --cutoff_len 8192
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

    HF_S2_BASE="SaFD-00/${MODEL_SHORT}-${HF_SLUG[$DS]}stage2-base"
    HF_S2_WORLD="SaFD-00/${MODEL_SHORT}-${HF_SLUG[$DS]}stage2-${STAGE1_MODE}-world-model"

    if [ ! -f "$TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test file: $TEST_JSONL" >&2
      exit 1
    fi

    # ─────────────────────────────────────────────────────────────────────
    # Phase A. Baseline Zero-shot — vllm_infer (mode 무관)
    # ─────────────────────────────────────────────────────────────────────
    OUT_BASE_REL="${S2_EVAL_OUT_REL}/base"
    OUT_BASE="$LF_ROOT/$OUT_BASE_REL"
    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_baseline" \
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

    # ─────────────────────────────────────────────────────────────────────
    # Phase B. HF merged variants
    #   lora_base                      (mode 무관)
    #   lora_world_model_${MODE}       (상류 Stage 1 모드)
    # ─────────────────────────────────────────────────────────────────────
    declare -A VARIANT_HF=(
      [lora_base]="$HF_S2_BASE"
      [lora_world_model_${STAGE1_MODE}]="$HF_S2_WORLD"
    )

    for VARIANT in lora_base "lora_world_model_${STAGE1_MODE}"; do
      HF_MODEL="${VARIANT_HF[$VARIANT]}"
      OUT_VAR_REL="${S2_EVAL_OUT_REL}/${VARIANT}"
      OUT_VAR="$LF_ROOT/$OUT_VAR_REL"

      run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}" \
        bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_VAR_REL' && \
          python scripts/vllm_infer.py \
            --model_name_or_path '$HF_MODEL' \
            --dataset '$DS_TEST' \
            --dataset_dir '$LF_ROOT/data' \
            ${VLLM_COMMON_ARGS[*]} \
            --vllm_config '{\"gpu_memory_utilization\": 0.80}' \
            --save_name        '$OUT_VAR_REL/generated_predictions.jsonl' \
            --matrix_save_name '$OUT_VAR_REL/predict_results.json' && \
          python '$BASE_DIR/scripts/_action_eval.py' score \
            --test   '$TEST_JSONL' \
            --pred   '$OUT_VAR/generated_predictions.jsonl' \
            --output '$OUT_VAR/action_metrics.json'"
    done
  done
done
