#!/usr/bin/env bash
# Stage 1 Evaluation — HF Hub merged repo sweep × 교차 데이터셋.
#
# 학습 DS (TRAIN_DATASET, HF repo 식별) 와 평가 DS (EVAL_DATASETS, test JSONL)
# 를 분리한다. 학습한 모델 하나를 여러 벤치마크에서 sweep 할 수 있다.
#
# Flags (공통은 _common.sh::parse_eval_args 참고):
#   --model / --train-dataset / --eval-datasets
#   --variants LIST      콤마 구분. 기본: base,full_world_model,lora_world_model
#     base               : Zero-shot baseline (base model)
#     full_world_model   : SaFD-00/{short}-{slug}world-model-stage1-full-epoch{E}
#     lora_world_model   : SaFD-00/{short}-{slug}world-model-stage1-lora-epoch{E}
#   --epochs LIST        콤마 구분 정수 (기본 1,2,3). world-model variant 대상.
#
# EVAL_DS 별 테스트 파일:
#   AC / MC : data/{DATADIR}/gui-model_stage1_test.jsonl  (split_data.py 산출)
#   MB      : data/MobiBench/gui-model_stage1.jsonl       (단일 벤치마크 파일)
#
# 산출물 (TRAIN_DS 루트, EVAL_DS 별 on-{DS}/ 서브디렉토리):
#   outputs/{TRAIN_DS}/eval/{MODEL}/stage1_eval/base/on-{EVAL_DS}/
#       (generated_predictions|hungarian_metrics).json
#   outputs/{TRAIN_DS}/eval/{MODEL}/stage1_eval/{full|lora}_world_model/epoch-{E}/on-{EVAL_DS}/
#       (generated_predictions|hungarian_metrics).json

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_eval_args "$@"
resolve_stage1_variants
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage1_eval"
TRAIN_DS="$TRAIN_DATASET"

# EVAL_DS → (TEST_JSONL, DATASET_NAME) 조합.
# MB 는 단일 파일, AC/MC 는 split 된 *_test.jsonl.
stage1_eval_paths() {
  local eval_ds="$1"
  local datadir="${DS_DATADIR[$eval_ds]}"
  local prefix="${DS_PREFIX[$eval_ds]}"
  if [[ "$eval_ds" == "MB" ]]; then
    EVAL_TEST_JSONL="$BASE_DIR/data/${datadir}/gui-model_stage1.jsonl"
    EVAL_DATASET_NAME="${prefix}_stage1"
  else
    EVAL_TEST_JSONL="$BASE_DIR/data/${datadir}/gui-model_stage1_test.jsonl"
    EVAL_DATASET_NAME="${prefix}_stage1_test"
  fi
}

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

  EVAL_DIR_REL="../outputs/${TRAIN_DS}/eval/${MODEL_SHORT}/stage1_eval"

  for EVAL_DS in "${EVAL_DATASETS[@]}"; do
    stage1_eval_paths "$EVAL_DS"

    if [ ! -f "$EVAL_TEST_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][train=$TRAIN_DS][eval=$EVAL_DS] Missing test file: $EVAL_TEST_JSONL" >&2
      exit 1
    fi

    for VARIANT in "${VARIANTS[@]}"; do
      case "$VARIANT" in
        base)
          OUT_REL="${EVAL_DIR_REL}/base/on-${EVAL_DS}"
          OUT_DIR="$LF_ROOT/$OUT_REL"
          TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${TRAIN_DS}_base_on-${EVAL_DS}"
          if skip_if_done "$TAG" "$OUT_DIR/hungarian_metrics.json"; then continue; fi

          run_logged "$TAG" \
            bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_REL' && \
              python scripts/vllm_infer.py \
                --model_name_or_path '$BASE_MODEL' \
                --dataset '$EVAL_DATASET_NAME' \
                --dataset_dir '$LF_ROOT/data' \
                ${VLLM_COMMON_ARGS[*]} \
                --vllm_config '${VLLM_CONFIG}' \
                --save_name        '$OUT_REL/generated_predictions.jsonl' \
                --matrix_save_name '$OUT_REL/predict_results.json' && \
              python '$BASE_DIR/scripts/_hungarian_eval.py' score \
                --test   '$EVAL_TEST_JSONL' \
                --pred   '$OUT_DIR/generated_predictions.jsonl' \
                --output '$OUT_DIR/hungarian_metrics.json'"
          ;;

        full_world_model|lora_world_model)
          MODE="${VARIANT%_world_model}"    # full | lora
          echo "[+] [$MODEL_SHORT][train=$TRAIN_DS][eval=$EVAL_DS][$VARIANT] Sweeping epochs: ${EPOCHS[*]}" >&2
          for EPOCH in "${EPOCHS[@]}"; do
            HUB_ID=$(hf_repo_id_stage1 "$MODEL_SHORT" "$TRAIN_DS" "$MODE" "$EPOCH")
            OUT_REL="${EVAL_DIR_REL}/${VARIANT}/epoch-${EPOCH}/on-${EVAL_DS}"
            OUT_DIR="$LF_ROOT/$OUT_REL"
            TAG="${SCRIPT_TAG}_${MODEL_SHORT}_${TRAIN_DS}_${VARIANT}_epoch${EPOCH}_on-${EVAL_DS}"
            if skip_if_done "$TAG" "$OUT_DIR/hungarian_metrics.json"; then continue; fi

            run_logged "$TAG" \
              bash -c "cd '$LF_ROOT' && mkdir -p '$OUT_REL' && \
                python scripts/vllm_infer.py \
                  --model_name_or_path '$HUB_ID' \
                  --dataset '$EVAL_DATASET_NAME' \
                  --dataset_dir '$LF_ROOT/data' \
                  ${VLLM_COMMON_ARGS[*]} \
                  --vllm_config '${VLLM_CONFIG}' \
                  --save_name        '$OUT_REL/generated_predictions.jsonl' \
                  --matrix_save_name '$OUT_REL/predict_results.json' && \
                python '$BASE_DIR/scripts/_hungarian_eval.py' score \
                  --test   '$EVAL_TEST_JSONL' \
                  --pred   '$OUT_DIR/generated_predictions.jsonl' \
                  --output '$OUT_DIR/hungarian_metrics.json'"
          done
          ;;
      esac
    done
  done
done
