#!/usr/bin/env bash
# Stage 2 Evaluation — HF Hub merged repo sweep (ID + OOD).
#
# Winner 개념이 제거되었다. 평가할 variant 와 epoch 을 명시적으로 지정한다.
# 평가는 항상 in-domain (test_id) 과 out-of-domain (test_ood) 두 파일로 수행되고,
# _action_eval.py 가 overall / in_domain / out_of_domain 3 섹션으로 집계한다.
#
# Flags:
#   --model / --dataset   (공통)
#   --variants LIST       콤마 구분. 기본: base,full_base,lora_base,full_world_model,lora_world_model
#     base                   : Zero-shot baseline (base model)
#     {full|lora}_base       : SaFD-00/{short}-{slug}base-stage2-{mode2}-epoch{E2}
#     {full|lora}_world_model: SaFD-00/{short}-{slug}world-model-stage1-{STAGE1_MODE}-epoch{STAGE1_EPOCH}-stage2-{mode2}-epoch{E2}
#   --epochs LIST         콤마 구분 정수 (기본 1,2,3). stage2 epoch sweep 대상.
#   --stage1-mode {full|lora}   world-model variant 에서 상류 Stage 1 모드 (기본 full).
#   --stage1-epoch N      world-model variant 에서 HF repo 계보 번호 주입용.
#                         world-model variant 평가 시 필수.
#
# Test 파일 요구:
#   data/{AndroidControl,MobiBench}/gui-model_stage2_test_id.jsonl
#   data/{AndroidControl,MobiBench}/gui-model_stage2_test_ood.jsonl
#   (split_data.py 가 생성)
#
# 산출물:
#   outputs/{DS}/eval/{MODEL}/stage2_eval/{variant}[/epoch-{E}]/
#     generated_predictions_id.jsonl
#     generated_predictions_ood.jsonl
#     predict_results_{id,ood}.json
#     action_metrics.json   (overall / in_domain / out_of_domain 3 섹션)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
resolve_stage2_variants
export DISABLE_VERSION_CHECK=1

SCRIPT_TAG="stage2_eval"

declare -A DS_DATADIR=( [MB]="MobiBench" [AC]="AndroidControl" )

# variant x epoch x {id, ood} 한 번의 inference 수행. 완료 후 action_metrics.json 산출.
run_variant_epoch_eval() {
  local model_short="$1" ds="$2" variant="$3" epoch="$4" hub_id="$5" \
        out_rel="$6" template="$7" test_id_jsonl="$8" test_ood_jsonl="$9" \
        prefix="${10}"
  local out_dir="$LF_ROOT/$out_rel"
  local tag="${SCRIPT_TAG}_${model_short}_${ds}_${variant}"
  if [[ -n "$epoch" ]]; then
    tag="${tag}_epoch${epoch}"
  fi
  if skip_if_done "$tag" "$out_dir/action_metrics.json"; then
    return 0
  fi

  local vllm_common=(
    --template "$template"
    --cutoff_len 8192
    --image_max_pixels 4233600
  )
  if [[ "$template" == qwen3_vl* ]]; then
    vllm_common+=(--enable_thinking False)
  fi
  local vllm_config='{"gpu_memory_utilization": 0.80}'

  local ds_test_id="${prefix}_stage2_test_id"
  local ds_test_ood="${prefix}_stage2_test_ood"

  run_logged "$tag" \
    bash -c "cd '$LF_ROOT' && mkdir -p '$out_rel' && \
      python scripts/vllm_infer.py \
        --model_name_or_path '$hub_id' \
        --dataset '$ds_test_id' \
        --dataset_dir '$LF_ROOT/data' \
        ${vllm_common[*]} \
        --vllm_config '${vllm_config}' \
        --save_name        '$out_rel/generated_predictions_id.jsonl' \
        --matrix_save_name '$out_rel/predict_results_id.json' && \
      python scripts/vllm_infer.py \
        --model_name_or_path '$hub_id' \
        --dataset '$ds_test_ood' \
        --dataset_dir '$LF_ROOT/data' \
        ${vllm_common[*]} \
        --vllm_config '${vllm_config}' \
        --save_name        '$out_rel/generated_predictions_ood.jsonl' \
        --matrix_save_name '$out_rel/predict_results_ood.json' && \
      python '$BASE_DIR/scripts/_action_eval.py' score \
        --test-id  '$test_id_jsonl' \
        --pred-id  '$out_dir/generated_predictions_id.jsonl' \
        --test-ood '$test_ood_jsonl' \
        --pred-ood '$out_dir/generated_predictions_ood.jsonl' \
        --output   '$out_dir/action_metrics.json'"
}

for MODEL_SHORT in "${MODELS[@]}"; do
  BASE_MODEL="${MODEL_ID[$MODEL_SHORT]}"
  TEMPLATE="${MODEL_TEMPLATE[$MODEL_SHORT]}"

  for DS in "${DATASETS[@]}"; do
    PREFIX="${DS_PREFIX[$DS]}"
    TEST_ID_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage2_test_id.jsonl"
    TEST_OOD_JSONL="$BASE_DIR/data/${DS_DATADIR[$DS]}/gui-model_stage2_test_ood.jsonl"
    EVAL_DIR_REL="../outputs/${DS}/eval/${MODEL_SHORT}/stage2_eval"

    if [ ! -f "$TEST_ID_JSONL" ] || [ ! -f "$TEST_OOD_JSONL" ]; then
      echo "[!] [$MODEL_SHORT][$DS] Missing test_id/test_ood jsonl — run split_data.py first:" >&2
      echo "      $TEST_ID_JSONL" >&2
      echo "      $TEST_OOD_JSONL" >&2
      exit 1
    fi

    for VARIANT in "${VARIANTS[@]}"; do
      case "$VARIANT" in
        base)
          OUT_REL="${EVAL_DIR_REL}/base"
          run_variant_epoch_eval "$MODEL_SHORT" "$DS" base "" "$BASE_MODEL" \
            "$OUT_REL" "$TEMPLATE" "$TEST_ID_JSONL" "$TEST_OOD_JSONL" "$PREFIX"
          ;;

        full_base|lora_base)
          MODE2="${VARIANT%_base}"
          echo "[+] [$MODEL_SHORT][$DS][$VARIANT] Sweeping stage2 epochs: ${EPOCHS[*]}" >&2
          for EPOCH in "${EPOCHS[@]}"; do
            HUB_ID=$(hf_repo_id_stage2_base "$MODEL_SHORT" "$DS" "$MODE2" "$EPOCH")
            OUT_REL="${EVAL_DIR_REL}/${VARIANT}/epoch-${EPOCH}"
            run_variant_epoch_eval "$MODEL_SHORT" "$DS" "$VARIANT" "$EPOCH" "$HUB_ID" \
              "$OUT_REL" "$TEMPLATE" "$TEST_ID_JSONL" "$TEST_OOD_JSONL" "$PREFIX"
          done
          ;;

        full_world_model|lora_world_model)
          if [[ -z "$STAGE1_EPOCH" ]]; then
            echo "[!] [$MODEL_SHORT][$DS][$VARIANT] --stage1-epoch 필수." >&2
            exit 2
          fi
          MODE2="${VARIANT%_world_model}"
          echo "[+] [$MODEL_SHORT][$DS][$VARIANT] stage1=${STAGE1_MODE}ep${STAGE1_EPOCH} stage2 epochs: ${EPOCHS[*]}" >&2
          for EPOCH in "${EPOCHS[@]}"; do
            HUB_ID=$(hf_repo_id_stage2_world_model "$MODEL_SHORT" "$DS" \
              "$STAGE1_MODE" "$STAGE1_EPOCH" "$MODE2" "$EPOCH")
            OUT_REL="${EVAL_DIR_REL}/${VARIANT}_from_${STAGE1_MODE}_ep${STAGE1_EPOCH}/epoch-${EPOCH}"
            run_variant_epoch_eval "$MODEL_SHORT" "$DS" "$VARIANT" "$EPOCH" "$HUB_ID" \
              "$OUT_REL" "$TEMPLATE" "$TEST_ID_JSONL" "$TEST_OOD_JSONL" "$PREFIX"
          done
          ;;
      esac
    done
  done
done
