#!/usr/bin/env bash
# Stage 2 Evaluation Pipeline — HF Hub merged 모델 sweep + winner 선택
#
# 순서 전환 (train → merge → eval) 이후, eval 은 stage2_merge.sh 가 이미
# HF Hub 에 푸시한 variant × epoch 별 merged repo 를 pull 해서 평가한다.
# 평가 대상 epoch 은 `--epochs` 플래그 (기본 1,2,3) 로 지정하며,
# 로컬 outputs/{DS}/adapters/.../checkpoint-*/ 는 조회하지 않는다.
#
# --stage1-mode full (default) | lora 는 world-model variant 의 HF repo prefix
# (`{MODE}-world-model`) 를 결정할 뿐, 로컬 merged 디렉토리에 의존하지 않는다.
#
# 3-Way:
#   base                                  - Zero-shot baseline (1 회, mode 무관)
#   lora_base                             - SaFD-00/...stage2-base-epoch{E}                (sweep)
#   lora_world_model_from_${MODE}         - SaFD-00/...stage2-${MODE}-world-model-epoch{E} (sweep)
#
# 각 lora 변형별로:
#   Phase B. epoch 별 HF repo → vllm_infer + _action_eval.py score
#   Phase C. winner 선택 (_action_eval.py select) → BEST_CHECKPOINT 파일 기록
#             (--hf-repo-template 로 winner hf_repo_id 를 JSON 에 주입)
#
# 산출물 (BASE_DIR 기준):
#   outputs/{DS}/eval/{MODEL}/stage2_eval/{base|lora_base|lora_world_model_from_${MODE}}/
#     base:    generated_predictions.jsonl | predict_results.json | action_metrics.json
#     variant: epoch-{E}/(generated_predictions|predict_results|action_metrics).json
#   outputs/{DS}/adapters/{MODEL}_stage2_{lora_base|lora_world_model_from_${MODE}}/
#     BEST_CHECKPOINT, BEST_CHECKPOINT.json (epoch, hf_repo_id 포함)

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

  # Merged 모델은 단일 모델이므로 LoRA 인자 불필요 (max_lora_rank 제거).
  SWEEP_VLLM_CONFIG='{"gpu_memory_utilization": 0.80}'

  for DS in "${DATASETS[@]}"; do
    PREFIX="${DS_PREFIX[$DS]}"
    DS_TEST="${PREFIX}_stage2_test"
    TEST_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage2_test.jsonl"
    S2_EVAL_OUT_REL="../outputs/${DS}/eval/${MODEL_SHORT}/stage2_eval"

    if [ ! -f "$TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test file: $TEST_JSONL" >&2
      exit 1
    fi

    # ─────────────────────────────────────────────────────────────────────
    # Phase A. Baseline Zero-shot — vllm_infer (1 회, mode 무관)
    # ─────────────────────────────────────────────────────────────────────
    OUT_BASE_REL="${S2_EVAL_OUT_REL}/base"
    OUT_BASE="$LF_ROOT/$OUT_BASE_REL"
    BASE_TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_baseline"
    if skip_if_done "$BASE_TAG" "$OUT_BASE/action_metrics.json"; then
      :
    else
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
    fi

    # ─────────────────────────────────────────────────────────────────────
    # Phase B + C. lora_base / lora_world_model_from_${MODE} 각각 HF sweep + select
    # ─────────────────────────────────────────────────────────────────────
    WORLD_VARIANT="lora_world_model_from_${STAGE1_MODE}"
    WORLD_ADAPTER_SUB="${MODEL_SHORT}_stage2_lora_world_model_from_${STAGE1_MODE}"

    declare -A VARIANT_ADAPTER_SUB=(
      [lora_base]="${MODEL_SHORT}_stage2_lora_base"
      [${WORLD_VARIANT}]="${WORLD_ADAPTER_SUB}"
    )
    declare -A VARIANT_HUB_SUFFIX=(
      [lora_base]="base"
      [${WORLD_VARIANT}]="${STAGE1_MODE}-world-model"
    )

    for VARIANT in lora_base "${WORLD_VARIANT}"; do
      ADAPTER_SUB="${VARIANT_ADAPTER_SUB[$VARIANT]}"
      LORA_DIR="$BASE_DIR/outputs/${DS}/adapters/${ADAPTER_SUB}"
      EVAL_DIR_REL="${S2_EVAL_OUT_REL}/${VARIANT}"
      EVAL_DIR="$LF_ROOT/$EVAL_DIR_REL"

      echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Sweeping epochs: ${EPOCHS[*]}" >&2

      SWEEP_RAN=0
      for EPOCH in "${EPOCHS[@]}"; do
        HUB_ID=$(hf_repo_id_stage2 "$MODEL_SHORT" "$DS" "${VARIANT_HUB_SUFFIX[$VARIANT]}" "$EPOCH")
        OUT_CKPT_REL="${EVAL_DIR_REL}/epoch-${EPOCH}"
        OUT_CKPT="$LF_ROOT/$OUT_CKPT_REL"
        EPOCH_TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}_epoch${EPOCH}"

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

      # Phase C: winner 선택 → BEST_CHECKPOINT 기록 (adapter 출력 디렉토리에)
      # winner metric: step_accuracy (AndroidControl 표준 정의)
      # Phase B 에서 새 평가가 하나도 없었고 BEST_CHECKPOINT.json 이 이미 있으면 skip.
      HUB_TEMPLATE=$(hf_repo_id_stage2 "$MODEL_SHORT" "$DS" "${VARIANT_HUB_SUFFIX[$VARIANT]}" '{epoch}')
      SELECT_TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}_select"
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

    unset VARIANT_ADAPTER_SUB VARIANT_HUB_SUFFIX
  done
done
