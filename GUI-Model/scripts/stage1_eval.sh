#!/usr/bin/env bash
# Stage 1 Evaluation Pipeline — HF Hub merged 모델 sweep + Hungarian winner
#
# 순서 전환 (train → merge → eval) 이후, eval 은 stage1_merge.sh 가 이미
# HF Hub 에 푸시한 epoch 별 merged repo 를 pull 해서 평가한다. 평가 대상 epoch
# 은 `--epochs` 플래그 (기본 1,2,3) 로 지정하며, 로컬 checkpoint 디렉토리는
# 조회하지 않는다.
#
# --stage1-mode full (default) | lora 에 따라 HF repo prefix 가 달라진다
# (hf_repo_id_stage1 단일 정의).
#
#   Phase A. Baseline Hungarian (zero-shot, $BASE_MODEL)        — mode 무관
#   Phase B. HF repo sweep (각 epoch 의 merged 모델)
#              - vllm_infer --model_name_or_path <HF repo id>   (full/lora 공통)
#   Phase C. Winner 선택 (_hungarian_eval.py select)
#              → adapters/{MODEL}_stage1_{MODE}/BEST_CHECKPOINT{.json}
#                (epoch, hf_repo_id 필드 포함)
#
# 산출물 (BASE_DIR 기준):
#   outputs/{DS}/eval/{MODEL}/stage1_eval/base/(generated_predictions|hungarian_metrics).json
#   outputs/{DS}/eval/{MODEL}/stage1_eval/${MODE}_world_model/epoch-{E}/(generated_predictions|hungarian_metrics).json
#   outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/BEST_CHECKPOINT       (plain text, "epoch-{E}")
#   outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/BEST_CHECKPOINT.json  (상세 순위 + hf_repo_id)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage1_eval_${STAGE1_MODE}"

# DS 코드 → data/ 아래 디렉토리명
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

  # Merged 모델은 full/lora 공통으로 단일 모델이므로 LoRA 인자 불필요.
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

    # ─────────────────────────────────────────────────────────────────────
    # Phase A. Baseline Hungarian — Zero-shot (mode 무관)
    # ─────────────────────────────────────────────────────────────────────
    OUT_BASE_REL="${EVAL_DIR_REL}/base"
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
        python '$BASE_DIR/scripts/_hungarian_eval.py' score \
          --test   '$TEST_JSONL' \
          --pred   '$OUT_BASE/generated_predictions.jsonl' \
          --output '$OUT_BASE/hungarian_metrics.json'"

    # ─────────────────────────────────────────────────────────────────────
    # Phase B. HF repo sweep — `--epochs` 로 받은 정수 리스트를 그대로 사용.
    #   hf_repo_id_stage1(MODEL, DS, MODE, E) 로 HF Hub merged repo id 를 조립.
    #   로컬 adapter 디렉토리 존재 여부와 무관하게 동작한다.
    # ─────────────────────────────────────────────────────────────────────
    echo "[+] [$MODEL_SHORT][$DS][$STAGE1_MODE] Sweeping epochs: ${EPOCHS[*]}" >&2

    for EPOCH in "${EPOCHS[@]}"; do
      HUB_ID=$(hf_repo_id_stage1 "$MODEL_SHORT" "$DS" "$STAGE1_MODE" "$EPOCH")
      OUT_CKPT_REL="${EVAL_DIR_REL}/${STAGE1_MODE}_world_model/epoch-${EPOCH}"
      OUT_CKPT="$LF_ROOT/$OUT_CKPT_REL"

      run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_epoch${EPOCH}" \
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
    done

    # ─────────────────────────────────────────────────────────────────────
    # Phase C. Winner 선택 → BEST_CHECKPOINT 파일 (adapter 디렉토리에 기록)
    #   --hf-repo-template 로 winner HF repo id 를 BEST_CHECKPOINT.json 에 주입.
    # ─────────────────────────────────────────────────────────────────────
    WIN_EVAL_DIR="$EVAL_DIR/${STAGE1_MODE}_world_model"
    HUB_TEMPLATE="$(hf_repo_id_stage1 "$MODEL_SHORT" "$DS" "$STAGE1_MODE" '{epoch}')"
    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_select" \
      python "$BASE_DIR/scripts/_hungarian_eval.py" select \
        --eval-dir  "$WIN_EVAL_DIR" \
        --train-dir "$TRAIN_DIR" \
        --metric    avg_hungarian_f1 \
        --hf-repo-template "$HUB_TEMPLATE"
  done
done
