#!/usr/bin/env bash
# Stage 1 Merge — 전체 epoch checkpoint 를 각각 merge + HF Hub push.
#
# train → merge → eval 흐름 전환: BEST_CHECKPOINT 의존 제거. 모든
# outputs/{OUT_DS}/adapters/{MODEL}{SFX}_stage1_{MODE}_world-model{VER}{SEG}/checkpoint-*/
# 를 순회하며 epoch 별로 local merge + 개별 HF repo push 한다.
# (OUT_DS = ds_outputs_code(DS), SFX = ds_model_suffix(DS) — AC_EXP01_ratio* → AndroidControl_EXP01 + _ratio{37,55,73})
#
# AC_EXP01: --dataset AC_EXP01 입력 시 parse_args 가
# DATASETS=(AC_EXP01_ratio37 AC_EXP01_ratio55 AC_EXP01_ratio73) 로 펼쳐, 모두 단일 부모
# outputs/AndroidControl_EXP01/ 아래에서 model dir 의 ratio suffix 로 분리된다.
# HF repo slug 는 (ac-exp01-ratio37-, ac-exp01-ratio55-, ac-exp01-ratio73-) 그대로 ratio 별로 push.
# 부분 실행은 --exp01-ratios ratio55,ratio73.
#
# --stage1-mode full (default) | lora.
# --no-hf-upload 시 local merge 만 수행하고 HF Hub push 는 생략.
#
# 임시 merge YAML 생성 → llamafactory-cli export
# (lora 모드는 base model + adapter_name_or_path 블록 추가,
#  --no-hf-upload 시 export_hub_model_id 를 생략)
#
# --stage1-variant VARIANT: stage1 ablation 계보 (기본 없음 = 메인 stage1).
# 세그먼트가 어댑터 입력 경로·merged 출력 경로·HF repo id **셋 다**에 들어간다 —
# 하나라도 빠지면 에러 없이 메인 런 산출물을 읽거나 덮어쓴다 (아래 불변식 가드).
# 명명 규칙 정본은 _common.sh::stage1_variant_seg.
#
# HF repo id 규칙 (단일 정의: _common.sh::hf_repo_id_stage1):
#   SaFD-00/{short}-{slug}world-model{SEG}-stage1-{MODE}-epoch{E}
#
# 로컬 산출물 (사용자 정책: 전부 보존):
#   outputs/{OUT_DS}/merged/{MODEL}{SFX}_stage1_{MODE}_world-model{VER}{SEG}/epoch-{E}/
#
# 요구: HF Hub upload 시 HF_TOKEN (.env 또는 환경변수)

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1

# stage1 ablation 계보 세그먼트 ("" | -action-only | …). 명명 규칙 정본은
# _common.sh::stage1_variant_seg 이고 여기서는 그 값을 경로 조립에 쓰기만 한다.
VARSEG="$(stage1_variant_seg "$STAGE1_VARIANT")"

# 불변식 가드 — variant 를 지정했는데 최종 문자열에 세그먼트가 없으면 **merge 하기 전에** 죽는다.
# 이 스크립트에는 한때 "--stage1-variant 를 아예 거절" 하는 임시 가드가 있었다. 그 가드가
# 막고 있던 진짜 사고는 "variant 를 아는 척하면서 메인 런 어댑터를 merge 해 메인 런 HF repo 를
# 덮어쓰는 것" 이므로, 배선이 끝난 지금은 그 조건을 직접 검사한다.
#   · adapters 입력 경로 (TRAIN_DIR_REL) — 잘못되면 **엉뚱한 체크포인트**를 merge 한다
#   · merged 출력 경로   (MERGED_REL)    — 잘못되면 메인 런 merged 를 덮어쓴다
#   · HF repo id         (HUB_ID)        — 잘못되면 HF 의 메인 런 체크포인트를 되돌릴 수 없게 덮어쓴다
# HUB_ID 는 --no-hf-upload 에서 빈 문자열이므로 업로드할 때만 검사한다.
assert_variant_segment() {
  local what="$1" value="$2"
  [[ -z "$VARSEG" ]] && return 0
  if [[ "$value" != *"$VARSEG"* ]]; then
    echo "[!] stage1 variant '$STAGE1_VARIANT' 가 지정됐는데 ${what} 에 세그먼트 '${VARSEG}' 가 없습니다:" >&2
    echo "      $value" >&2
    echo "    메인 런 산출물을 읽거나 덮어쓸 수 있어 중단합니다 (_common.sh::stage1_variant_seg 참조)." >&2
    exit 1
  fi
}

SCRIPT_TAG="stage1_merge_${STAGE1_MODE}${STAGE1_VARIANT:+_$STAGE1_VARIANT}"
MERGED_COUNT=0
SKIPPED_COUNT=0
FAILED_COUNT=0

for MODEL_SHORT in "${MODELS[@]}"; do
  BASE_MODEL="${MODEL_ID[$MODEL_SHORT]}"
  for DS in "${DATASETS[@]}"; do
    # AC_EXP01 ratio variant 는 outputs/AndroidControl_EXP01/ 단일 부모 + model dir 에 _ratio{37,55,73} suffix.
    OUT_DS="$(ds_outputs_code "$DS")"
    SFX="$(ds_model_suffix "$DS")"
    # VER(_v1): model-variant 이름 맨 끝 버전 태그 (EXP07). LOCAL_DIR(local_merged_epoch_dir)
    # 가 DS 로 버전을 붙이므로 여기 REL 경로도 같은 버전으로 맞춘다. 그 외 DS 는 빈 문자열.
    VER="$(ds_version_suffix "$DS")"
    # LF cwd 기준 상대경로 (= BASE_DIR 기준 "outputs/...").
    # VARSEG 는 VER 다음에 온다 — gen_configs.render_stage1 이 output_dir 를
    # `save_s1_{mode}` + `-{variant}` 로 만들기 때문에 학습이 실제로 쓴 디렉토리와
    # 바이트 일치해야 한다 (어긋나면 다른 계보의 체크포인트를 merge 한다).
    TRAIN_DIR_REL="../outputs/${OUT_DS}/adapters/${MODEL_SHORT}${SFX}_stage1_${STAGE1_MODE}_world-model${VER}${VARSEG}"
    TRAIN_DIR="$LF_ROOT/$TRAIN_DIR_REL"
    assert_variant_segment "adapters 입력 경로" "$TRAIN_DIR_REL"

    shopt -s nullglob
    CKPTS=("$TRAIN_DIR"/checkpoint-*/)
    shopt -u nullglob
    if [ "${#CKPTS[@]}" -eq 0 ]; then
      echo "[WARN] [$MODEL_SHORT][$DS][$STAGE1_MODE] No checkpoints under $TRAIN_DIR — skipping. Run stage1_train.sh --stage1-mode ${STAGE1_MODE} first." >&2
      SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
      continue
    fi
    echo "[+] [$MODEL_SHORT][$DS][$STAGE1_MODE] Merging ${#CKPTS[@]} checkpoints" >&2

    for CKPT_DIR in "${CKPTS[@]}"; do
      CKPT_DIR="${CKPT_DIR%/}"
      CKPT_NAME=$(basename "$CKPT_DIR")
      EPOCH=$(ckpt_epoch_from_dir "$CKPT_DIR") || {
        echo "[!] [$MODEL_SHORT][$DS][$STAGE1_MODE][$CKPT_NAME] epoch 파싱 실패" >&2
        FAILED_COUNT=$((FAILED_COUNT + 1)); continue
      }

      if [ "$HF_UPLOAD" -eq 1 ]; then
        HUB_ID=$(hf_repo_id_stage1 "$MODEL_SHORT" "$DS" "$STAGE1_MODE" "$EPOCH" "$STAGE1_VARIANT")
        assert_variant_segment "HF repo id" "$HUB_ID"
        TARGET_DESC="$HUB_ID"
      else
        HUB_ID=""
        TARGET_DESC="local-only"
      fi
      MERGED_REL="../outputs/${OUT_DS}/merged/${MODEL_SHORT}${SFX}_stage1_${STAGE1_MODE}_world-model${VER}${VARSEG}/epoch-${EPOCH}"
      LOCAL_DIR="$(local_merged_epoch_dir stage1 "$MODEL_SHORT" "$DS" "$STAGE1_MODE" "$EPOCH" "$STAGE1_VARIANT")"
      assert_variant_segment "merged 출력 경로" "$MERGED_REL"
      assert_variant_segment "merged 출력 경로(절대)" "$LOCAL_DIR"
      CKPT_REL="./${TRAIN_DIR_REL}/${CKPT_NAME}"

      echo "[+] [$MODEL_SHORT][$DS][$STAGE1_MODE] ${CKPT_NAME} (epoch=${EPOCH}) → ${TARGET_DESC}" >&2

      TMP_YAML=$(mktemp -t "stage1_merge_${MODEL_SHORT}_${DS}_${STAGE1_MODE}_ep${EPOCH}_XXXXXX.yaml")
      if [ "$STAGE1_MODE" = "full" ]; then
        cat > "$TMP_YAML" <<EOF
### model
model_name_or_path: ${CKPT_REL}
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
model_name_or_path: ${BASE_MODEL}
adapter_name_or_path: ${CKPT_REL}
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

      if run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}_epoch${EPOCH}" \
        bash -c "cd '$LF_ROOT' && llamafactory-cli export '$TMP_YAML'"; then
        :
      else
        FAILED_COUNT=$((FAILED_COUNT + 1))
        rm -f "$TMP_YAML"
        continue
      fi
      rm -f "$TMP_YAML"

      if [ ! -d "$LOCAL_DIR" ]; then
        echo "[!] [$MODEL_SHORT][$DS][$STAGE1_MODE][epoch${EPOCH}] Expected output dir missing: $LOCAL_DIR" >&2
        FAILED_COUNT=$((FAILED_COUNT + 1))
        continue
      fi
      MERGED_COUNT=$((MERGED_COUNT + 1))
    done
  done
done

echo "--- Stage 1 Merge (${STAGE1_MODE}): $MERGED_COUNT merged, $SKIPPED_COUNT skipped, $FAILED_COUNT failed ---" >&2
if [ "$FAILED_COUNT" -gt 0 ]; then
  echo "[!] Some epochs failed. Re-run after fixing." >&2
  exit 1
fi
if [ "$MERGED_COUNT" -eq 0 ] && [ "$SKIPPED_COUNT" -eq 0 ]; then
  echo "[!] No models were merged." >&2
  exit 1
fi
