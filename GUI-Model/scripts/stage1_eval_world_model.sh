#!/usr/bin/env bash
# Stage 1 Evaluation (world_model only) — HF Hub merged repo sweep + winner 선택.
#
# stage1_eval.sh 의 Phase B + C 를 떼어낸 경량 스크립트. baseline 은 건너뛴다.
# --stage1-mode full (default) | lora 에 따라 HF repo prefix 가 달라진다
# (hf_repo_id_stage1 단일 정의).
#
#   HF repo:   SaFD-00/{short}-{slug}stage1-{MODE}-world-model-epoch{E}
#   Adapter:   outputs/{DS}/adapters/{MODEL}_stage1_{MODE}/
#   Eval out:  outputs/{DS}/eval/{MODEL}/stage1_eval/{MODE}_world_model/
#                ├ epoch-{E}/generated_predictions.jsonl
#                ├ epoch-{E}/predict_results.json
#                └ epoch-{E}/hungarian_metrics.json
#   Winner:    adapter dir 에 BEST_CHECKPOINT, BEST_CHECKPOINT.json
#              (metric=avg_hungarian_f1, hf_repo_id 주입)
#
# 사용:
#   bash ./scripts/stage1_eval_world_model.sh --model qwen2.5-vl-7b --dataset AC
#   bash ./scripts/stage1_eval_world_model.sh --model qwen2.5-vl-7b --dataset AC --epochs 1,2,3
#   bash ./scripts/stage1_eval_world_model.sh --model qwen2.5-vl-7b --dataset AC --stage1-mode lora

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage1_eval_world_model_${STAGE1_MODE}"

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
    DS_TEST="${PREFIX}_stage1_test"
    TEST_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage1_test.jsonl"
    TRAIN_DIR_REL="../outputs/${DS}/adapters/${MODEL_SHORT}_stage1_${STAGE1_MODE}"
    EVAL_DIR_REL="../outputs/${DS}/eval/${MODEL_SHORT}/stage1_eval"
    TRAIN_DIR="$LF_ROOT/$TRAIN_DIR_REL"
    EVAL_DIR="$LF_ROOT/$EVAL_DIR_REL"

    if [ ! -f "$TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test file: $TEST_JSONL" >&2
      exit 1
    fi

    echo "[+] [$MODEL_SHORT][$DS][$STAGE1_MODE] Sweeping epochs: ${EPOCHS[*]}" >&2

    SWEEP_RAN=0
    for EPOCH in "${EPOCHS[@]}"; do
      HUB_ID=$(hf_repo_id_stage1 "$MODEL_SHORT" "$DS" "$STAGE1_MODE" "$EPOCH")
      OUT_CKPT_REL="${EVAL_DIR_REL}/${STAGE1_MODE}_world_model/epoch-${EPOCH}"
      OUT_CKPT="$LF_ROOT/$OUT_CKPT_REL"
      EPOCH_TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_epoch${EPOCH}"

      if skip_if_done "$EPOCH_TAG" "$OUT_CKPT/hungarian_metrics.json"; then
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
          python '$BASE_DIR/scripts/_hungarian_eval.py' score \
            --test   '$TEST_JSONL' \
            --pred   '$OUT_CKPT/generated_predictions.jsonl' \
            --output '$OUT_CKPT/hungarian_metrics.json'"
      SWEEP_RAN=1
    done

    WIN_EVAL_DIR="$EVAL_DIR/${STAGE1_MODE}_world_model"
    HUB_TEMPLATE="$(hf_repo_id_stage1 "$MODEL_SHORT" "$DS" "$STAGE1_MODE" '{epoch}')"
    SELECT_TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_select"
    if (( SWEEP_RAN == 0 )) && skip_if_done "$SELECT_TAG" "$TRAIN_DIR/BEST_CHECKPOINT.json"; then
      :
    else
      run_logged "$SELECT_TAG" \
        python "$BASE_DIR/scripts/_hungarian_eval.py" select \
          --eval-dir  "$WIN_EVAL_DIR" \
          --train-dir "$TRAIN_DIR" \
          --metric    avg_hungarian_f1 \
          --hf-repo-template "$HUB_TEMPLATE"
    fi
  done
done
