#!/usr/bin/env bash
# Stage 2 Merge — 전체 epoch adapter/checkpoint 를 각각 merge + HF Hub push.
#
# Variants:
#   base_${STAGE2_MODE}          - Base model + (full|lora) Stage 2 checkpoint
#   world-model_from_${STAGE1_MODE}-ep${STAGE1_EPOCH}_${STAGE2_MODE}
#                                - Stage 1 local merged (epoch = --stage1-epoch) +
#                                  (full|lora) Stage 2 checkpoint
#
# Flags (all required):
#   --stage1-mode {full|lora}    Stage 1 상류 모델 종류 (world-model variant 전용)
#   --stage1-epoch N             Stage 1 local merged/{MODEL}_stage1_{MODE}_world-model/epoch-N
#                                world-model variant 전용. base variant 에서는 무시.
#   --stage1-variant V           Stage 1 ablation 계보 (기본 없음 = 메인 stage1).
#                                --stage1-epoch 와 **같은 축**이다: 상류 stage1 을 지목하고
#                                그 계보를 stage2 adapters/merged 경로와 HF repo id 에 새긴다.
#                                base variant 에서는 무시 (stage1 계보가 없다).
#   --stage2-mode {full|lora}    Stage 2 학습 방식 (adapter 디렉토리 + HF suffix 결정)
#   --no-hf-upload               local merge 만 수행하고 HF Hub push 는 생략
#   --variants LIST              콤마 구분. base | world_model | adapter 중 일부만 merge.
#                                기본은 전부. **어휘가 stage2_train.sh/stage2_eval.sh 와
#                                다르다** (여기는 adapter 디렉토리 키다).
#   --model / --dataset          (공통)
#
# HF repo id 규칙 (단일 정의: _common.sh):
#   base variant:
#     SaFD-00/{short}-{slug}base-stage2-{STAGE2_MODE}-epoch{E2}
#   world-model variant:
#     SaFD-00/{short}-{slug}world-model{SEG}-stage1-{STAGE1_MODE}-epoch{E1}-stage2-{STAGE2_MODE}-epoch{E2}
#     (SEG = stage1_variant_seg(--stage1-variant), 미지정 시 "" → 기존 문자열 불변)
#
# 로컬 산출물 (전부 보존):
#   outputs/{DS}/merged/{MODEL}_stage2_{STAGE2_MODE}_{base|world-model{SEG}_from_{STAGE1_MODE}-ep{E1}}/epoch-{E2}/
#
# 임시 merge YAML → llamafactory-cli export
#   · full:  model_name_or_path=ckpt (adapter 블록 없음)
#   · lora:  model_name_or_path=base + adapter_name_or_path=ckpt
#
# 요구: HF Hub upload 시 HF_TOKEN (.env 또는 환경변수)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

# stage1 ablation 계보 세그먼트 ("" | -action-only | …). 명명 규칙 정본은
# _common.sh::stage1_variant_seg — 여기서는 조립에만 쓴다.
VARSEG="$(stage1_variant_seg "$STAGE1_VARIANT")"

# 불변식 가드 (stage1_merge.sh 와 같은 이유·같은 형태): variant 를 지정했는데 최종
# 문자열에 세그먼트가 없으면 merge 전에 죽는다. stage2 에서 위험한 자리는 셋이다 —
# 상류 stage1 merged 경로(잘못된 base 로 merge), stage2 adapters/merged 경로(다른 계보의
# 체크포인트를 읽거나 덮어씀), HF repo id(HF 산출물 덮어씀).
# HUB_ID 는 --no-hf-upload 에서 빈 문자열이므로 업로드할 때만 검사한다.
assert_variant_segment() {
  local what="$1" value="$2"
  [[ -z "$VARSEG" ]] && return 0
  if [[ "$value" != *"$VARSEG"* ]]; then
    echo "[!] stage1 variant '$STAGE1_VARIANT' 가 지정됐는데 ${what} 에 세그먼트 '${VARSEG}' 가 없습니다:" >&2
    echo "      $value" >&2
    echo "    다른 stage1 계보의 산출물을 읽거나 덮어쓸 수 있어 중단합니다 (_common.sh::stage1_variant_seg 참조)." >&2
    exit 1
  fi
}

SCRIPT_TAG="stage2_merge_${STAGE2_MODE}_from_${STAGE1_MODE}${STAGE1_VARIANT:+_$STAGE1_VARIANT}"
MERGED_COUNT=0
FAILED_COUNT=0
SKIPPED_COUNT=0

for MODEL_SHORT in "${MODELS[@]}"; do
  BASE_MODEL="${MODEL_ID[$MODEL_SHORT]}"

  for DS in "${DATASETS[@]}"; do
    # AC_EXP01 ratio variant: outputs/AndroidControl_EXP01 단일 부모 + model dir 에 _ratio{37,55,73} suffix.
    OUT_DS="$(ds_outputs_code "$DS")"
    SFX="$(ds_model_suffix "$DS")"
    # VER(_v1): stage2 model-variant 이름 맨 끝 버전 태그 (EXP07). LOCAL_DIR 와 맞춘다.
    VER="$(ds_version_suffix "$DS")"

    # Stage 1 local merged base (world-model variant 전용). --stage1-epoch 기반.
    # ds_stage1_source 로 stage1 계보 소스 DS 를 해석한다 (예: AC_EXP06 → AC_EXP05,
    # stage2 비증강 대조군은 EXP06 stage1 을 따로 학습하지 않고 EXP05 를 승계).
    # stage2 산출물(OUT_DS/SFX, 위에서 정의)은 원래 DS 를 그대로 유지 — stage1 winner 참조만 소스 기준.
    S1_SRC_DS="$(ds_stage1_source "$DS")"
    S1_OUT_DS="$(ds_outputs_code "$S1_SRC_DS")"
    S1_SFX="$(ds_model_suffix "$S1_SRC_DS")"
    # S1_VER(_v1): stage1 merged model-variant 버전 태그. S1_WINNER_ABS(local_merged_epoch_dir)
    # 가 S1_SRC_DS 로 버전을 붙이므로 world-model variant 의 base 로 쓰는 REL 도 같은 버전으로 맞춘다.
    S1_VER="$(ds_version_suffix "$S1_SRC_DS")"
    S1_WINNER_AVAILABLE=0
    S1_WINNER_ABS=""
    S1_WINNER_REL=""
    if [[ -n "$STAGE1_EPOCH" ]]; then
      # VARSEG 는 S1_VER 다음 (stage1_merge.sh 가 만든 merged 디렉토리와 같은 규칙).
      S1_WINNER_ABS="$(local_merged_epoch_dir stage1 "$MODEL_SHORT" "$S1_SRC_DS" "$STAGE1_MODE" "$STAGE1_EPOCH" "$STAGE1_VARIANT")"
      S1_WINNER_REL="../outputs/${S1_OUT_DS}/merged/${MODEL_SHORT}${S1_SFX}_stage1_${STAGE1_MODE}_world-model${S1_VER}${VARSEG}/epoch-${STAGE1_EPOCH}"
      assert_variant_segment "상류 stage1 merged 경로" "$S1_WINNER_REL"
      if [ -d "$S1_WINNER_ABS" ]; then
        S1_WINNER_AVAILABLE=1
      else
        echo "[WARN] [$MODEL_SHORT][$DS] Missing Stage 1 merged dir (stage1 소스 DS: ${S1_SRC_DS}): $S1_WINNER_ABS" >&2
        echo "       world-model variant 건너뜁니다. (base variant 는 계속 진행)" >&2
      fi
    else
      echo "[WARN] [$MODEL_SHORT][$DS] --stage1-epoch 미지정 → world-model variant 건너뜁니다." >&2
    fi

    # variant key (adapter 디렉토리 suffix 로 사용)
    # world-model 계보 키에는 상류 stage1 을 지목하는 축이 둘이다 — epoch(-ep{E1}) 과
    # ablation variant(VARSEG). 세그먼트는 world-model 토큰 바로 뒤 (stage1_variant_seg 정본).
    # 이 키는 stage2_train.sh 가 쓴 adapters 디렉토리 이름과 바이트 일치해야 한다.
    BASE_VARIANT_KEY="${STAGE2_MODE}_base"
    WM_VARIANT_KEY="${STAGE2_MODE}_world-model${VARSEG}_from_${STAGE1_MODE}-ep${STAGE1_EPOCH}"
    # merge X 계보 (world-model-adapter): stage1 어댑터를 병합하지 않고 base 위에 얹어
    # 이어학습한 stage2 어댑터. merge 는 원본 base + 그 stage2 ckpt 로 수행 (stage1 merged 불필요).
    ADAPTER_VARIANT_KEY="${STAGE2_MODE}_world-model${VARSEG}_from_adapter-ep${STAGE1_EPOCH}"

    declare -A VARIANT_BASE_LF_REL=(
      [base]="$BASE_MODEL"
      [world_model]="${S1_WINNER_REL}"
      [adapter]="$BASE_MODEL"
    )
    declare -A VARIANT_ADAPTER_SUFFIX=(
      [base]="${BASE_VARIANT_KEY}"
      [world_model]="${WM_VARIANT_KEY}"
      [adapter]="${ADAPTER_VARIANT_KEY}"
    )

    # merge X 변형은 lora×lora + 해당 YAML(EXP07) 이 있을 때만 편입 (EXP01–06 merge 불변).
    # adapter merge 는 stage1 merged 를 요구하지 않으므로 S1_WINNER 게이트를 타지 않는다;
    # 자기 stage2 ckpt 부재 시 아래 CKPTS 체크로 자연스럽게 skip.
    VARIANTS_TO_MERGE=(base world_model)
    if [[ "$STAGE2_MODE" == "lora" && "$STAGE1_MODE" == "lora" && -n "$STAGE1_EPOCH" \
          && -f "$BASE_DIR/configs/train/$(ds_config_subfolder "$DS")/stage2_lora/${MODEL_SHORT}_world-model-adapter$(ds_version_suffix "$DS").yaml" ]]; then
      VARIANTS_TO_MERGE+=(adapter)
    fi

    # --variants 로 일부 계보만 merge (stage2_train.sh 의 같은 이름 필터와 대칭이되
    # **어휘가 다르다** — train 은 YAML variant 이름(base|world-model-{full,lora}),
    # 여기는 adapter 디렉토리 키(base|world_model|adapter)다.
    #
    # 이 필터가 필요한 이유: base 계보와 world-model 계보를 GPU 쌍마다 따로
    # (train→merge→eval) 돌릴 때, 필터가 없으면 먼저 끝난 세션의 merge 가 아직
    # 학습 중인 다른 계보까지 훑고, 뒤이어 끝난 세션의 merge 가 **이미 merge 된
    # 계보를 같은 export_dir 로 다시 쓴다** — 그 디렉토리를 평가가 읽고 있으면
    # 조용히 깨진 모델을 로드한다. 계보를 세션에 못박아 그 교차를 없앤다.
    if [[ "${#VARIANTS[@]}" -gt 0 ]]; then
      # 오타를 조용한 no-op 으로 흘리지 않는다 — 이 스크립트의 어휘는 셋뿐이다.
      for w in "${VARIANTS[@]}"; do
        case "$w" in
          base|world_model|adapter) ;;
          *)
            echo "[!] stage2_merge.sh --variants 는 base | world_model | adapter 만 받습니다 (got '$w')." >&2
            echo "    (stage2_train.sh 의 world-model-full / stage2_eval.sh 의 lora_world_model 과 어휘가 다릅니다.)" >&2
            exit 2 ;;
        esac
      done
      FILTERED=()
      for v in "${VARIANTS_TO_MERGE[@]}"; do
        for w in "${VARIANTS[@]}"; do
          if [[ "$v" == "$w" ]]; then FILTERED+=("$v"); break; fi
        done
      done
      VARIANTS_TO_MERGE=("${FILTERED[@]}")
    fi

    for VARIANT in "${VARIANTS_TO_MERGE[@]}"; do
      if [ "$VARIANT" = "world_model" ] && [ "$S1_WINNER_AVAILABLE" -eq 0 ]; then
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
        continue
      fi

      ADAPTER_SUFFIX="${VARIANT_ADAPTER_SUFFIX[$VARIANT]}"
      TRAIN_DIR="$BASE_DIR/outputs/${OUT_DS}/adapters/${MODEL_SHORT}${SFX}_stage2_${ADAPTER_SUFFIX}${VER}"
      TRAIN_DIR_REL="../outputs/${OUT_DS}/adapters/${MODEL_SHORT}${SFX}_stage2_${ADAPTER_SUFFIX}${VER}"
      # base 계보는 stage1 을 잇지 않으므로 세그먼트가 **없는 것이 정상**이다 —
      # 불변식 가드는 world-model 계보(world_model|adapter)에만 건다.
      if [[ "$VARIANT" != base ]]; then
        assert_variant_segment "stage2 adapters 입력 경로" "$TRAIN_DIR_REL"
      fi

      shopt -s nullglob
      CKPTS=("$TRAIN_DIR"/checkpoint-*/)
      shopt -u nullglob
      if [ "${#CKPTS[@]}" -eq 0 ]; then
        echo "[WARN] [$MODEL_SHORT][$DS][stage2_${ADAPTER_SUFFIX}] No checkpoints under $TRAIN_DIR — skipping. Run stage2_train.sh first." >&2
        SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
        continue
      fi
      echo "[+] [$MODEL_SHORT][$DS][stage2_${ADAPTER_SUFFIX}] Merging ${#CKPTS[@]} checkpoints" >&2

      for CKPT_DIR in "${CKPTS[@]}"; do
        CKPT_DIR="${CKPT_DIR%/}"
        CKPT_NAME=$(basename "$CKPT_DIR")
        EPOCH=$(ckpt_epoch_from_dir "$CKPT_DIR") || {
          echo "[!] [$MODEL_SHORT][$DS][stage2_${ADAPTER_SUFFIX}][$CKPT_NAME] epoch 파싱 실패" >&2
          FAILED_COUNT=$((FAILED_COUNT + 1)); continue
        }

        if [ "$HF_UPLOAD" -eq 1 ]; then
          case "$VARIANT" in
            base)
              HUB_ID=$(hf_repo_id_stage2_base "$MODEL_SHORT" "$DS" "$STAGE2_MODE" "$EPOCH") ;;
            adapter)
              HUB_ID=$(hf_repo_id_stage2_world_model_adapter "$MODEL_SHORT" "$DS" \
                "$STAGE1_MODE" "$STAGE1_EPOCH" "$STAGE2_MODE" "$EPOCH" "$STAGE1_VARIANT")
              assert_variant_segment "HF repo id" "$HUB_ID" ;;
            *)
              HUB_ID=$(hf_repo_id_stage2_world_model "$MODEL_SHORT" "$DS" \
                "$STAGE1_MODE" "$STAGE1_EPOCH" "$STAGE2_MODE" "$EPOCH" "$STAGE1_VARIANT")
              assert_variant_segment "HF repo id" "$HUB_ID" ;;
          esac
          TARGET_DESC="$HUB_ID"
        else
          HUB_ID=""
          TARGET_DESC="local-only"
        fi
        MERGED_REL="../outputs/${OUT_DS}/merged/${MODEL_SHORT}${SFX}_stage2_${ADAPTER_SUFFIX}${VER}/epoch-${EPOCH}"
        LOCAL_DIR="$(local_merged_epoch_dir stage2 "$MODEL_SHORT" "$DS" "$ADAPTER_SUFFIX" "$EPOCH")"
        if [[ "$VARIANT" != base ]]; then
          assert_variant_segment "stage2 merged 출력 경로" "$MERGED_REL"
        fi
        ADAPTER_REL="${TRAIN_DIR_REL}/${CKPT_NAME}"

        echo "[+] [$MODEL_SHORT][$DS][stage2_${ADAPTER_SUFFIX}] ${CKPT_NAME} (epoch=${EPOCH}) → ${TARGET_DESC}" >&2

        TMP_YAML=$(mktemp -t "stage2_merge_${MODEL_SHORT}_${DS}_${VARIANT}_ep${EPOCH}_XXXXXX.yaml")
        if [ "$STAGE2_MODE" = "full" ]; then
          # Full FT: checkpoint 자체가 이미 전체 모델 → adapter 없음.
          cat > "$TMP_YAML" <<EOF
### model
model_name_or_path: ${ADAPTER_REL}
trust_remote_code: true
template: ${MODEL_TEMPLATE[$MODEL_SHORT]}

### export
export_dir: ${MERGED_REL}
export_size: 5
export_device: cpu
export_legacy_format: false
EOF
        else
          cat > "$TMP_YAML" <<EOF
### model
model_name_or_path: ${VARIANT_BASE_LF_REL[$VARIANT]}
adapter_name_or_path: ${ADAPTER_REL}
trust_remote_code: true
finetuning_type: lora
template: ${MODEL_TEMPLATE[$MODEL_SHORT]}

### export
export_dir: ${MERGED_REL}
export_size: 5
export_device: cpu
export_legacy_format: false
EOF
        fi
        if [ "$HF_UPLOAD" -eq 1 ]; then
          cat >> "$TMP_YAML" <<EOF
export_hub_model_id: ${HUB_ID}
EOF
        fi
        if ! run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_${VARIANT}_epoch${EPOCH}" \
          bash -c "cd '$LF_ROOT' && llamafactory-cli export '$TMP_YAML'"; then
          FAILED_COUNT=$((FAILED_COUNT + 1))
          rm -f "$TMP_YAML"
          continue
        fi
        rm -f "$TMP_YAML"

        if [ ! -d "$LOCAL_DIR" ]; then
          echo "[!] [$MODEL_SHORT][$DS][stage2_${ADAPTER_SUFFIX}][epoch${EPOCH}] Expected output dir missing: $LOCAL_DIR" >&2
          FAILED_COUNT=$((FAILED_COUNT + 1))
          continue
        fi
        MERGED_COUNT=$((MERGED_COUNT + 1))
      done
    done

    unset VARIANT_BASE_LF_REL VARIANT_ADAPTER_SUFFIX
  done
done

echo "--- Stage 2 Merge (stage2=${STAGE2_MODE} from stage1=${STAGE1_MODE}): $MERGED_COUNT merged, $SKIPPED_COUNT skipped, $FAILED_COUNT failed ---" >&2
if [ "$FAILED_COUNT" -gt 0 ]; then
  echo "[!] Some epochs failed. Re-run after fixing." >&2
  exit 1
fi
if [ "$MERGED_COUNT" -eq 0 ] && [ "$SKIPPED_COUNT" -eq 0 ]; then
  echo "[!] No variants were merged." >&2
  exit 1
fi
