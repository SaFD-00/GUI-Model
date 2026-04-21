#!/usr/bin/env bash
# Stage 1 Evaluation — HF Hub merged repo sweep.
#
# Winner 개념이 제거되었다. 평가할 variant 와 epoch 을 명시적으로 지정한다.
#
# Flags:
#   --model / --dataset   (공통)
#   --variants LIST       콤마 구분. 기본: base,full_world_model,lora_world_model
#     base                : Zero-shot baseline (base model)
#     full_world_model    : SaFD-00/{short}-{slug}world-model-stage1-full-epoch{E}
#     lora_world_model    : SaFD-00/{short}-{slug}world-model-stage1-lora-epoch{E}
#   --epochs LIST         콤마 구분 정수 (기본 1,2,3). world-model variant 대상.
#
# 산출물:
#   outputs/{DS}/eval/{MODEL}/stage1_eval/base/(generated_predictions|hungarian_metrics).json
#   outputs/{DS}/eval/{MODEL}/stage1_eval/{full|lora}_world_model/epoch-{E}/
#       (generated_predictions|hungarian_metrics).json

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
resolve_stage1_variants
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage1_eval"

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

  VLLM_CONFIG='{"gpu_memory_utilization": 0.80}'

  for DS in "${DATASETS[@]}"; do
    PREFIX="${DS_PREFIX[$DS]}"
    DS_TEST="${PREFIX}_stage1_test"
    TEST_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage1_test.jsonl"
    EVAL_DIR_REL="../outputs/${DS}/eval/${MODEL_SHORT}/stage1_eval"
    EVAL_DIR="$LF_ROOT/$EVAL_DIR_REL"

    if [ ! -f "$TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test file: $TEST_JSONL" >&2
      exit 1
    fi

    for VARIANT in "${VARIANTS[@]}"; do
      case "$VARIANT" in
        base)
          OUT_REL="${EVAL_DIR_REL}/base"
          OUT_DIR="$LF_ROOT/$OUT_REL"
          TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_base"
          if skip_if_done "$TAG" "$OUT_DIR/hungarian_metrics.json"; then continue; fi

          run_logged "$TAG" \
            bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_REL' && \
              python scripts/vllm_infer.py \
                --model_name_or_path '$BASE_MODEL' \
                --dataset '$DS_TEST' \
                --dataset_dir '$LF_ROOT/data' \
                ${VLLM_COMMON_ARGS[*]} \
                --vllm_config '${VLLM_CONFIG}' \
                --save_name        '$OUT_REL/generated_predictions.jsonl' \
                --matrix_save_name '$OUT_REL/predict_results.json' && \
              python '$BASE_DIR/scripts/_hungarian_eval.py' score \
                --test   '$TEST_JSONL' \
                --pred   '$OUT_DIR/generated_predictions.jsonl' \
                --output '$OUT_DIR/hungarian_metrics.json'"
          ;;

        full_world_model|lora_world_model)
          MODE="${VARIANT%_world_model}"    # full | lora
          echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Sweeping epochs: ${EPOCHS[*]}" >&2
          for EPOCH in "${EPOCHS[@]}"; do
            HUB_ID=$(hf_repo_id_stage1 "$MODEL_SHORT" "$DS" "$MODE" "$EPOCH")
            OUT_REL="${EVAL_DIR_REL}/${VARIANT}/epoch-${EPOCH}"
            OUT_DIR="$LF_ROOT/$OUT_REL"
            TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}_epoch${EPOCH}"
            if skip_if_done "$TAG" "$OUT_DIR/hungarian_metrics.json"; then continue; fi

            run_logged "$TAG" \
              bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_REL' && \
                python scripts/vllm_infer.py \
                  --model_name_or_path '$HUB_ID' \
                  --dataset '$DS_TEST' \
                  --dataset_dir '$LF_ROOT/data' \
                  ${VLLM_COMMON_ARGS[*]} \
                  --vllm_config '${VLLM_CONFIG}' \
                  --save_name        '$OUT_REL/generated_predictions.jsonl' \
                  --matrix_save_name '$OUT_REL/predict_results.json' && \
                python '$BASE_DIR/scripts/_hungarian_eval.py' score \
                  --test   '$TEST_JSONL' \
                  --pred   '$OUT_DIR/generated_predictions.jsonl' \
                  --output '$OUT_DIR/hungarian_metrics.json'"
          done
          ;;
      esac
    done
  done
done
