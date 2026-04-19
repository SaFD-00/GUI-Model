#!/usr/bin/env bash
# Stage 2 Evaluation Pipeline — 로컬 adapter checkpoint sweep (mode-aware) + winner 선택
#
# --stage1-mode full (default) | lora 는 world-model variant 의 상류를 선택:
#   world-model base = outputs/{DS}/merged/{MODEL}_stage1_${MODE}  (로컬 merged)
#   world-model adapters = outputs/{DS}/adapters/{MODEL}_stage2_lora_world_model_from_${MODE}/
#
# 3-Way:
#   base                                  - Zero-shot baseline (1 회, mode 무관)
#   lora_base                             - $BASE_MODEL + adapters/{MODEL}_stage2_lora_base/checkpoint-*          (sweep)
#   lora_world_model_from_${MODE}         - stage1_merged_${MODE} + adapters/{…}_stage2_lora_world_model_from_${MODE}/checkpoint-* (sweep)
#
# 각 lora 변형별로:
#   Phase B. 체크포인트 sweep (vllm_infer + _action_eval.py score)
#   Phase C. winner 선택 (_action_eval.py select) → BEST_CHECKPOINT 파일 기록
#
# 전제:
#   stage1_merge.sh --stage1-mode ${MODE} 로 outputs/{DS}/merged/{MODEL}_stage1_${MODE}/ 생성 (world-model variant 만 의존)
#   stage2_train.sh 로 adapter checkpoint-* 생성
#
# 산출물 (BASE_DIR 기준):
#   outputs/{DS}/eval/{MODEL}/stage2_eval/{base|lora_base|lora_world_model_from_${MODE}}/
#     base:    generated_predictions.jsonl | predict_results.json | action_metrics.json
#     variant: checkpoint-N/(generated_predictions|predict_results|action_metrics).json
#   outputs/{DS}/adapters/{MODEL}_stage2_{lora_base|lora_world_model_from_${MODE}}/
#     BEST_CHECKPOINT, BEST_CHECKPOINT.json

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_eval_from_${STAGE1_MODE}"

declare -A DS_DATADIR=( [MB]="MobiBench" [AC]="AndroidControl" )
# notebook _DATASET_CONFIG.stage2.lora_rank 와 일치 (vLLM max_lora_rank 기본값 16 초과시 ValueError)
declare -A DS_LORA_RANK=( [MB]=16 [AC]=32 )

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
    LORA_RANK="${DS_LORA_RANK[$DS]}"

    STAGE1_MERGED_REL="../outputs/${DS}/merged/${MODEL_SHORT}_stage1_${STAGE1_MODE}"
    STAGE1_MERGED_ABS="$BASE_DIR/outputs/${DS}/merged/${MODEL_SHORT}_stage1_${STAGE1_MODE}"

    if [ ! -f "$TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test file: $TEST_JSONL" >&2
      exit 1
    fi

    # ─────────────────────────────────────────────────────────────────────
    # Phase A. Baseline Zero-shot — vllm_infer (1 회, mode 무관)
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
    # Phase B + C. lora_base / lora_world_model_from_${MODE} 각각 sweep + select
    #   - lora_base:           $BASE_MODEL                  + adapters/{M}_stage2_lora_base
    #   - lora_world_model_…:  $STAGE1_MERGED_REL (local)    + adapters/{M}_stage2_lora_world_model_from_${MODE}
    # ─────────────────────────────────────────────────────────────────────
    WORLD_VARIANT="lora_world_model_from_${STAGE1_MODE}"
    WORLD_ADAPTER_SUB="${MODEL_SHORT}_stage2_lora_world_model_from_${STAGE1_MODE}"

    declare -A VARIANT_BASE=(
      [lora_base]="$BASE_MODEL"
      [${WORLD_VARIANT}]="$STAGE1_MERGED_REL"
    )
    declare -A VARIANT_ADAPTER_SUB=(
      [lora_base]="${MODEL_SHORT}_stage2_lora_base"
      [${WORLD_VARIANT}]="${WORLD_ADAPTER_SUB}"
    )

    for VARIANT in lora_base "${WORLD_VARIANT}"; do
      # world-model variant 는 stage1 merged 로컬 디렉토리 선행 필요
      if [ "$VARIANT" = "${WORLD_VARIANT}" ] && [ ! -d "$STAGE1_MERGED_ABS" ]; then
        echo "[!] [$MODEL_SHORT][$DS][$VARIANT] Missing $STAGE1_MERGED_ABS — run stage1_merge.sh --stage1-mode ${STAGE1_MODE} first." >&2
        exit 1
      fi

      VARIANT_BASE_MODEL="${VARIANT_BASE[$VARIANT]}"
      LORA_DIR_REL="../outputs/${DS}/adapters/${VARIANT_ADAPTER_SUB[$VARIANT]}"
      LORA_DIR="$LF_ROOT/$LORA_DIR_REL"
      EVAL_DIR_REL="${S2_EVAL_OUT_REL}/${VARIANT}"
      EVAL_DIR="$LF_ROOT/$EVAL_DIR_REL"

      shopt -s nullglob
      CKPTS=("$LORA_DIR"/checkpoint-*/)
      shopt -u nullglob
      if [ "${#CKPTS[@]}" -eq 0 ]; then
        echo "[!] [$MODEL_SHORT][$DS][$VARIANT] No checkpoints under $LORA_DIR — run stage2_train.sh first." >&2
        exit 1
      fi
      echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Sweeping ${#CKPTS[@]} checkpoints" >&2

      # Phase B: 체크포인트별 predict + score
      for CKPT_DIR in "${CKPTS[@]}"; do
        CKPT_NAME=$(basename "$CKPT_DIR")
        OUT_CKPT_REL="${EVAL_DIR_REL}/${CKPT_NAME}"
        OUT_CKPT="$LF_ROOT/$OUT_CKPT_REL"
        ADAPTER_REL="${LORA_DIR_REL}/${CKPT_NAME}"

        run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}_${CKPT_NAME}" \
          bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_CKPT_REL' && \
            python scripts/vllm_infer.py \
              --model_name_or_path   '$VARIANT_BASE_MODEL' \
              --adapter_name_or_path '$ADAPTER_REL' \
              --dataset '$DS_TEST' \
              --dataset_dir '$LF_ROOT/data' \
              ${VLLM_COMMON_ARGS[*]} \
              --vllm_config '{\"gpu_memory_utilization\": 0.80, \"max_lora_rank\": $LORA_RANK}' \
              --save_name        '$OUT_CKPT_REL/generated_predictions.jsonl' \
              --matrix_save_name '$OUT_CKPT_REL/predict_results.json' && \
            python '$BASE_DIR/scripts/_action_eval.py' score \
              --test   '$TEST_JSONL' \
              --pred   '$OUT_CKPT/generated_predictions.jsonl' \
              --output '$OUT_CKPT/action_metrics.json'"
      done

      # Phase C: winner 선택 → BEST_CHECKPOINT 기록 (adapter 출력 디렉토리에)
      # winner metric: step_accuracy (AndroidControl 표준 정의)
      run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}_select" \
        python "$BASE_DIR/scripts/_action_eval.py" select \
          --eval-dir  "$EVAL_DIR" \
          --train-dir "$LORA_DIR" \
          --metric    step_accuracy
    done

    unset VARIANT_BASE VARIANT_ADAPTER_SUB
  done
done
