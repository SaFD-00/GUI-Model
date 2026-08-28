#!/usr/bin/env bash
# Stage 1 Fine-tuning (full / lora)
# - --stage1-mode full (default) 또는 lora 로 선택.
# - FORCE_TORCHRUN=1 NPROC_PER_NODE=${NPROC_PER_NODE} (DeepSpeed Z3)
#
# AC_EXP01 (AndroidControl_EXP01): --dataset AC_EXP01 입력 시 _common.sh::parse_args 가
# DATASETS=(AC_EXP01_ratio37 AC_EXP01_ratio55 AC_EXP01_ratio73) 로 펼쳐주므로, 본 스크립트는
# ratio 별 DS 키를 그대로 받아
# configs/train/IWM-AC_EXP01_ratio{37,55,73}/stage1_{full,lora}/{MODEL_SHORT}_world-model.yaml
# 을 require_yaml 한다 (implicit_world_modeling.gen_configs 가 ratio 별 디렉토리에 YAML 을 생성).
# --exp01-ratios ratio55 처럼 부분 sweep 도 가능.
#
# NPROC_PER_NODE 은 .env 에서 관리 (기본값 2).
#
# --stage1-variant VARIANT: AC_EXP08 stage1 ablation 변형 선택 (기본: 없음 = 메인 stage1).
# 현재 action-only 하나뿐 — state 40K 없이 메인 stage1 이 본 것과 같은 action 10K 만으로
# 학습하는 stage1 full FT 대조군 (World Modeling 순효과 측정, lf_registry.py 의
# AndroidControl_EXP08::stage1_extra_variants 가 정본). 미지정 시 YAML 경로는 기존과
# byte-exact 로 동일하다.

# shellcheck source=./_common.sh
source "$(dirname "$0")/_common.sh"
parse_args "$@"
export DISABLE_VERSION_CHECK=1
: "${NPROC_PER_NODE:=2}"

# DeepSpeed CPUAdam JIT 빌드에 필요한 CUDA 라이브러리 경로 (conda env 내 nvidia 패키지)
# LIBRARY_PATH: 빌드 타임 링커, LD_LIBRARY_PATH: 런타임 로더
_NVIDIA_PKGS="$CONDA_PREFIX/lib/python3.12/site-packages/nvidia"
_CUDA_LIBS="${_NVIDIA_PKGS}/curand/lib:${_NVIDIA_PKGS}/cuda_runtime/lib"
export LIBRARY_PATH="${_CUDA_LIBS}:${LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="${_CUDA_LIBS}:${LD_LIBRARY_PATH:-}"

# variant 세그먼트 — gen_configs.py::generate_all 이 만드는 파일명
# "{model}_world-model-{variant}{cfg_ver}.yaml" 과 대칭. 미지정(기본)이면 빈 문자열이라
# 기존 "{model}_world-model{cfg_ver}.yaml" 경로가 byte-exact 로 유지된다.
_VARIANT_SUFFIX=""
[[ -n "$STAGE1_VARIANT" ]] && _VARIANT_SUFFIX="-${STAGE1_VARIANT}"

SCRIPT_TAG="stage1_train_${STAGE1_MODE}${STAGE1_VARIANT:+_$STAGE1_VARIANT}"

for MODEL_SHORT in "${MODELS[@]}"; do
  for DS in "${DATASETS[@]}"; do
    # YAML 정본은 repo 가 소유한다 (LF/examples/custom 이 아니라 configs/train).
    YAML="$BASE_DIR/configs/train/$(ds_config_subfolder "$DS")/stage1_${STAGE1_MODE}/${MODEL_SHORT}_world-model${_VARIANT_SUFFIX}$(ds_version_suffix "$DS").yaml"
    require_model_eligible "$MODEL_SHORT" "${DS_DATADIR[$DS]}"
    require_yaml "$YAML" "python -m implicit_world_modeling.gen_configs --write 로 생성하세요"

    # GPU 트리오(pdbs/grad_accum/deepspeed) + repo-owned dataset_dir/media_dir 를 런타임 주입.
    OVERRIDES="$(resolve_overrides "$MODEL_SHORT" "${DS_DATADIR[$DS]}" "$STAGE1_MODE")"
    echo_resolved "$YAML" "$OVERRIDES"
    maybe_dry_run "$YAML" "$OVERRIDES" && continue

    run_logged "${SCRIPT_TAG}_${MODEL_SHORT}_${DS}" \
      env FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE="$NPROC_PER_NODE" \
          PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
      bash -c "cd '$LF_ROOT' && llamafactory-cli train '$YAML' $OVERRIDES"
  done
done
