#!/usr/bin/env bash
# Stage 2 Evaluation (world_model only) — 레거시 네이밍 `lora_world_model` 평가.
#
# stage2_eval.sh 의 Phase B + C 를 떼어내 world_model variant 에만 특화.
# stage2_merge_world_model.sh 가 푸시한 HF repo 를 epoch 별로 sweep 한다.
#
#   HF repo:   SaFD-00/{short}-{slug}stage2-world-model-epoch{E}
#              (hf_repo_id_stage2 variant_suffix="world-model")
#   Adapter:   outputs/{DS}/adapters/{MODEL}_stage2_lora_world_model/
#   Eval out:  outputs/{DS}/eval/{MODEL}/stage2_eval/lora_world_model/
#                ├ epoch-{E}/generated_predictions.jsonl
#                ├ epoch-{E}/predict_results.json
#                └ epoch-{E}/action_metrics.json
#   Winner:    adapter dir 에 BEST_CHECKPOINT, BEST_CHECKPOINT.json 기록
#              (metric=step_accuracy, hf_repo_id 주입)
#
# 사용:
#   bash ./scripts/stage2_eval_world_model.sh --model qwen2.5-vl-7b --dataset AC
#   bash ./scripts/stage2_eval_world_model.sh --model qwen2.5-vl-7b --dataset AC --epochs 1,2,3

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_eval_world_model"
VARIANT="lora_world_model"
HUB_SUFFIX="world-model"

declare -A DS_DATADIR=( [MB]="MobiBench" [AC]="AndroidControl" )

for MODEL_SHORT in "${MODELS[@]}"; do
  TEMPLATE="${MODEL_TEMPLATE[$MODEL_SHORT]}"

  VLLM_COMMON_ARGS=(
    --template "$TEMPLATE"
    --cutoff_len 10240
    --image_max_pixels 4233600
  )
  if [[ "$TEMPLATE" == qwen3_vl* ]]; then
    VLLM_COMMON_ARGS+=(--enable_thinking False)
  fi

  SWEEP_VLLM_CONFIG='{"gpu_memory_utilization": 0.80}'

  for DS in "${DATASETS[@]}"; do
    PREFIX="${DS_PREFIX[$DS]}"
    DS_TEST="${PREFIX}_stage2_test"
    TEST_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage2_test.jsonl"

    if [ ! -f "$TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test file: $TEST_JSONL" >&2
      exit 1
    fi

    ADAPTER_SUB="${MODEL_SHORT}_stage2_${VARIANT}"
    LORA_DIR="$BASE_DIR/outputs/${DS}/adapters/${ADAPTER_SUB}"
    S2_EVAL_OUT_REL="../outputs/${DS}/eval/${MODEL_SHORT}/stage2_eval"
    EVAL_DIR_REL="${S2_EVAL_OUT_REL}/${VARIANT}"
    EVAL_DIR="$LF_ROOT/$EVAL_DIR_REL"

    echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Sweeping epochs: ${EPOCHS[*]}" >&2

    SWEEP_RAN=0
    for EPOCH in "${EPOCHS[@]}"; do
      HUB_ID=$(hf_repo_id_stage2 "$MODEL_SHORT" "$DS" "$HUB_SUFFIX" "$EPOCH")
      OUT_CKPT_REL="${EVAL_DIR_REL}/epoch-${EPOCH}"
      OUT_CKPT="$LF_ROOT/$OUT_CKPT_REL"
      EPOCH_TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_epoch${EPOCH}"

      if skip_if_done "$EPOCH_TAG" "$OUT_CKPT/action_metrics.json"; then
        continue
      fi

      run_logged "$EPOCH_TAG" \
        bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_CKPT_REL' && \
          python scripts/vllm_infer.py \
            --model_name_or_path '$HUB_ID' \
            --dataset '$DS_TEST' \
            --dataset_dir '$LF_ROOT/data' \
            ${VLLM_COMMON_ARGS[*]} \
            --vllm_config '${SWEEP_VLLM_CONFIG}' \
            --save_name        '$OUT_CKPT_REL/generated_predictions.jsonl' \
            --matrix_save_name '$OUT_CKPT_REL/predict_results.json' && \
          python '$BASE_DIR/scripts/_action_eval.py' score \
            --test   '$TEST_JSONL' \
            --pred   '$OUT_CKPT/generated_predictions.jsonl' \
            --output '$OUT_CKPT/action_metrics.json'"
      SWEEP_RAN=1
    done

    HUB_TEMPLATE=$(hf_repo_id_stage2 "$MODEL_SHORT" "$DS" "$HUB_SUFFIX" '{epoch}')
    SELECT_TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_select"
    if (( SWEEP_RAN == 0 )) && skip_if_done "$SELECT_TAG" "$LORA_DIR/BEST_CHECKPOINT.json"; then
      :
    else
      run_logged "$SELECT_TAG" \
        python "$BASE_DIR/scripts/_action_eval.py" select \
          --eval-dir  "$EVAL_DIR" \
          --train-dir "$LORA_DIR" \
          --metric    step_accuracy \
          --hf-repo-template "$HUB_TEMPLATE"
    fi
  done
done
