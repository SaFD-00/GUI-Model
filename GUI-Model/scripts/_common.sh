#!/usr/bin/env bash
# Shared helpers for GUI-Model stage{1,2}_{train,eval,merge}.sh
# Source from sibling scripts:  source "$(dirname "$0")/_common.sh"
# Requires: bash 4+ (associative array 사용). Linux 기본 bash 는 4+ 이므로 통상 OK.
#           macOS 기본 bash 는 3.2 → `brew install bash` 후 `/opt/homebrew/bin/bash` 권장.

set -euo pipefail

# 이 환경은 일부 deps (typing_extensions, regex, fsspec, peft, trl, deepspeed 등)
# 가 PYTHONUSERBASE 아래에만 설치되어 있으므로 user-site 는 비활성화하지 않는다.
# 다만 /root/.local/workspace/python-packages/bin 의 낡은 accelerate CLI 는
# shebang 이 base env python 을 가리킬 때가 있어 `No module named 'torch'` 를
# 유발한다. conda env 가 활성화되어 있다면 해당 env 의 bin 을 PATH 맨 앞에 고정해
# env 소속 CLI (/root/anaconda3/envs/gui-model/bin/accelerate 등) 가 먼저
# 잡히도록 강제한다.
if [[ -n "${CONDA_PREFIX:-}" ]]; then
  export PATH="$CONDA_PREFIX/bin:$PATH"
else
  echo "[!] conda env 가 활성화되어 있지 않습니다. 'conda activate gui-model' 후 다시 실행하세요." >&2
  exit 1
fi

if (( BASH_VERSINFO[0] < 4 )); then
  echo "[!] bash 4+ required (current: $BASH_VERSION)." >&2
  echo "    macOS 기본 /bin/bash 3.2 는 지원하지 않습니다. 'brew install bash' 후 재실행하세요." >&2
  exit 1
fi

# --- paths -------------------------------------------------------------------
# scripts/ 의 부모 디렉토리가 BASE_DIR (notebook Cell 3 의 BASE_DIR 대응)
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LF_ROOT="$BASE_DIR/LlamaFactory"
UNSLOTH_ROOT="$BASE_DIR/unsloth"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"

# --- data symlinks --------------------------------------
# vllm_infer.py 의 media_dir 는 dataset_dir 를 기본으로 사용.
# JSONL 이미지 경로가 "AndroidControl/images/..." 형태이므로
# LF_ROOT/data/ 아래에 심볼릭 링크가 필요함.
# 주의: eval script 에서 vllm_infer.py 호출 시 반드시 --dataset_dir '$LF_ROOT/data'
#       (절대 경로)를 전달해야 한다. 상대 경로("data") 사용 시 HF datasets 캐시가
#       다른 cwd 에서 생성된 stale 경로를 재사용하여 이미지 FileNotFoundError 발생.
for _ds_dir in "$BASE_DIR"/data/*/; do
  _ds_name=$(basename "$_ds_dir")
  _link="$LF_ROOT/data/$_ds_name"
  if [ ! -e "$_link" ]; then
    ln -sfn "$_ds_dir" "$_link"
  fi
done
unset _ds_dir _ds_name _link

# --- .env (HF_TOKEN 등) -------------------------------------------------------
if [ -f "$BASE_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$BASE_DIR/.env"
  set +a
fi

# --- dataset prefix / HF slug 매핑 (Cell 3 _DATASET_CONFIG 와 일치) -----------
declare -A DS_PREFIX=( [MB]="GUI-Model-MB" [AC]="GUI-Model-AC" )
declare -A HF_SLUG=(   [MB]="mb-"          [AC]="ac-"           )

# --- 모델 레지스트리 (Cell 3 _MODEL_CONFIG 와 일치) ---------------------------
declare -A MODEL_ID=(
  [qwen2-vl-2b]="Qwen/Qwen2-VL-2B-Instruct"
  [qwen2-vl-7b]="Qwen/Qwen2-VL-7B-Instruct"
  [qwen2.5-vl-3b]="Qwen/Qwen2.5-VL-3B-Instruct"
  [qwen2.5-vl-7b]="Qwen/Qwen2.5-VL-7B-Instruct"
  [qwen3-vl-2b]="Qwen/Qwen3-VL-2B-Instruct"
  [qwen3-vl-4b]="Qwen/Qwen3-VL-4B-Instruct"
  [qwen3-vl-8b]="Qwen/Qwen3-VL-8B-Instruct"
  [gemma-4-e2b]="unsloth/gemma-4-E2B-it"
  [gemma-4-e4b]="unsloth/gemma-4-E4B-it"
  [llava-v1.6-mistral-7b]="llava-hf/llava-v1.6-mistral-7b-hf"
  [llava-v1.6-vicuna-7b]="llava-hf/llava-v1.6-vicuna-7b-hf"
  [llama3-llava-next-8b]="llava-hf/llama3-llava-next-8b-hf"
)
declare -A MODEL_TEMPLATE=(
  [qwen2-vl-2b]="qwen2_vl"
  [qwen2-vl-7b]="qwen2_vl"
  [qwen2.5-vl-3b]="qwen2_vl"
  [qwen2.5-vl-7b]="qwen2_vl"
  [qwen3-vl-2b]="qwen3_vl_nothink"
  [qwen3-vl-4b]="qwen3_vl_nothink"
  [qwen3-vl-8b]="qwen3_vl_nothink"
  [gemma-4-e2b]="gemma4"
  [gemma-4-e4b]="gemma4"
  [llava-v1.6-mistral-7b]="llava_next"
  [llava-v1.6-vicuna-7b]="llava_next"
  [llama3-llava-next-8b]="llava_next"
)
# 정렬 순서: Qwen 이전세대→최신세대, Google, LLaVA-HF. 세대 내 작은 모델 먼저.
ALL_MODELS=(
  qwen2-vl-2b qwen2-vl-7b
  qwen2.5-vl-3b qwen2.5-vl-7b
  qwen3-vl-2b qwen3-vl-4b qwen3-vl-8b
  gemma-4-e2b gemma-4-e4b
  llava-v1.6-mistral-7b llava-v1.6-vicuna-7b llama3-llava-next-8b
)

# --- 학습 백엔드 매핑 (notebook _MODEL_CONFIG["backend"] 와 일치) --------------
# 미지정 모델은 기본값 "llamafactory" 로 분기된다.
# Gemma-4 계열은 Unsloth (https://github.com/unslothai/unsloth) 사용.
declare -A MODEL_BACKEND=(
  [gemma-4-e2b]="unsloth"
  [gemma-4-e4b]="unsloth"
)
get_backend() {
  local m="$1"
  echo "${MODEL_BACKEND[$m]:-llamafactory}"
}

# --- CLI 인자 파싱: --model MODEL --dataset DS --stage1-mode full|lora --------
# 사용법:
#   bash script.sh --model qwen3-vl-8b --dataset MB
#   bash script.sh --model qwen3-vl-8b --stage1-mode lora
#   bash script.sh --stage1-mode lora           # 전체 모델 LoRA 학습/평가/merge
#   bash script.sh                               # 기본값: 전체 모델 + 전체 DS + full
parse_args() {
  local model_arg="all"
  local dataset_arg="all"
  local stage1_mode_arg="full"
  local epochs_arg="1,2,3"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --model)
        if [[ -z "${2:-}" ]]; then echo "Error: --model requires a value." >&2; exit 2; fi
        model_arg="$2"; shift 2 ;;
      --dataset)
        if [[ -z "${2:-}" ]]; then echo "Error: --dataset requires a value." >&2; exit 2; fi
        dataset_arg="$2"; shift 2 ;;
      --stage1-mode)
        if [[ -z "${2:-}" ]]; then echo "Error: --stage1-mode requires a value." >&2; exit 2; fi
        stage1_mode_arg="$2"; shift 2 ;;
      --epochs)
        if [[ -z "${2:-}" ]]; then echo "Error: --epochs requires a value." >&2; exit 2; fi
        epochs_arg="$2"; shift 2 ;;
      -h|--help)
        cat <<EOF
Usage: $(basename "$0") [--model MODEL] [--dataset DS] [--stage1-mode MODE] [--epochs LIST]

Options:
  --model MODEL        모델 short_name 또는 "all" (기본: all)
  --dataset DS         MB | AC | all (기본: all)
  --stage1-mode MODE   full | lora (기본: full)
                       Stage 1 학습 방식. Stage 2 스크립트에서는 world-model variant
                       가 참조할 상류 Stage 1 소스를 선택.
  --epochs LIST        콤마로 구분된 epoch 정수 리스트 (기본: 1,2,3)
                       stage{1,2}_eval.sh 전용 — HF Hub merged repo sweep 대상.
                       다른 스크립트에서는 무시됨.
  -h, --help           이 도움말 표시

Available models:
  ${ALL_MODELS[*]}

Examples:
  $(basename "$0") --model qwen3-vl-8b --dataset MB
  $(basename "$0") --model gemma-4-e2b --stage1-mode lora
  $(basename "$0") --model qwen3-vl-8b --dataset AC --epochs 1,2,3
  $(basename "$0") --stage1-mode lora
  $(basename "$0")
EOF
        exit 0
        ;;
      *)
        echo "Error: Unknown argument '$1'. Use --help for usage." >&2
        exit 2
        ;;
    esac
  done

  case "$stage1_mode_arg" in
    full|lora) STAGE1_MODE="$stage1_mode_arg" ;;
    *)
      echo "Error: Unknown --stage1-mode '$stage1_mode_arg'. Use full | lora." >&2
      exit 2
      ;;
  esac

  # model_arg → MODELS 배열
  if [[ "$model_arg" == "all" ]]; then
    MODELS=("${ALL_MODELS[@]}")
  elif [[ -n "${MODEL_ID[$model_arg]+x}" ]]; then
    MODELS=("$model_arg")
  else
    echo "Error: Unknown model '$model_arg'." >&2
    echo "Available: ${ALL_MODELS[*]} | all" >&2
    exit 2
  fi

  # dataset_arg → DATASETS 배열
  case "$dataset_arg" in
    MB)  DATASETS=(MB) ;;
    AC)  DATASETS=(AC) ;;
    all) DATASETS=(MB AC) ;;
    *)
      echo "Error: Unknown dataset '$dataset_arg'. Use MB | AC | all." >&2
      exit 2
      ;;
  esac

  # epochs_arg → EPOCHS 배열 (stage{1,2}_eval.sh 에서 HF Hub sweep 대상으로 사용)
  IFS=',' read -r -a EPOCHS <<< "$epochs_arg"
  if [[ "${#EPOCHS[@]}" -eq 0 ]]; then
    echo "Error: --epochs 값이 비어있습니다." >&2
    exit 2
  fi
  for _e in "${EPOCHS[@]}"; do
    if ! [[ "$_e" =~ ^[0-9]+$ ]]; then
      echo "Error: --epochs 는 콤마로 구분된 정수여야 합니다 (got: '$epochs_arg')." >&2
      exit 2
    fi
  done
  unset _e
}

# --- (deprecated) 하위 호환용 ---
parse_dataset_arg() {
  local arg="${1:-all}"
  case "$arg" in
    MB) DATASETS=(MB) ;;
    AC) DATASETS=(AC) ;;
    all|"") DATASETS=(MB AC) ;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [MB|AC|all]  (deprecated: use --model/--dataset flags)
  MB   - MobiBench 만 실행
  AC   - AndroidControl 만 실행
  all  - 둘 다 순차 실행 (기본)
EOF
      exit 0
      ;;
    *)
      echo "Error: Unknown dataset '$arg'. Use MB | AC | all." >&2
      echo "Usage: $(basename "$0") [MB|AC|all]" >&2
      exit 2
      ;;
  esac
}

# --- tee 로거 ----------------------------------------------------------------
# usage: run_logged <tag> <cmd...>
# - LOG_DIR/<tag>_<timestamp>.log 로 저장
# - pipefail 로 커맨드 실패 시 스크립트 중단
run_logged() {
  local tag="$1"; shift
  local ts; ts="$(date +%Y%m%d_%H%M%S)"
  local log="$LOG_DIR/${tag}_${ts}.log"
  echo "[+] [$tag] start  -> log: $log" >&2
  echo "[+] [$tag] cmd:    $*" >&2
  local rc=0
  "$@" 2>&1 | tee "$log" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[!] [$tag] FAILED (exit=$rc)  log: $log" >&2
    exit "$rc"
  fi
  echo "[+] [$tag] done   log: $log" >&2
}

# --- YAML / 디렉토리 가드 ----------------------------------------------------
# usage: require_yaml <절대 또는 LF_ROOT 상대 경로> <노트북 cell 안내>
require_yaml() {
  local yaml="$1"; local hint="${2:-}"
  local abs
  if [[ "$yaml" == /* ]]; then abs="$yaml"; else abs="$LF_ROOT/$yaml"; fi
  if [ ! -f "$abs" ]; then
    echo "[!] Missing YAML: $abs" >&2
    [ -n "$hint" ] && echo "    Hint: $hint" >&2
    exit 1
  fi
}

# --- checkpoint → epoch 매핑 -------------------------------------------------
# HF Trainer 가 저장한 trainer_state.json 의 "epoch" 필드를 int 로 반환.
# 학습 YAML 은 save_strategy=epoch 이므로 정수에 근접하지만 방어적으로 round.
ckpt_epoch_from_dir() {
  local ckpt_dir="$1"
  local state="$ckpt_dir/trainer_state.json"
  if [ ! -f "$state" ]; then
    echo "[!] trainer_state.json not found: $state" >&2
    return 1
  fi
  python - "$state" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    s = json.load(f)
e = s.get("epoch")
if e is None:
    sys.stderr.write(f"[!] 'epoch' missing in {sys.argv[1]}\n")
    sys.exit(1)
print(int(round(float(e))))
PY
}

# --- HF Hub repo id 조립 (단일 실패 지점) ------------------------------------
# Stage 1: SaFD-00/{short}-{slug}stage1-{mode}-world-model-epoch{E}
hf_repo_id_stage1() {
  local model_short="$1" ds="$2" mode="$3" epoch="$4"
  printf 'SaFD-00/%s-%sstage1-%s-world-model-epoch%s' \
    "$model_short" "${HF_SLUG[$ds]}" "$mode" "$epoch"
}

# Stage 2: SaFD-00/{short}-{slug}stage2-{variant_suffix}-epoch{E}
#   variant_suffix ∈ {"base", "{mode}-world-model"}
hf_repo_id_stage2() {
  local model_short="$1" ds="$2" variant_suffix="$3" epoch="$4"
  printf 'SaFD-00/%s-%sstage2-%s-epoch%s' \
    "$model_short" "${HF_SLUG[$ds]}" "$variant_suffix" "$epoch"
}

# --- Local merged 디렉토리 경로 ---------------------------------------------
# stage1: merged/{MODEL}_stage1_{MODE}/epoch-{E}
# stage2: merged/{MODEL}_stage2_{variant_key}/epoch-{E}
#   variant_key 예: "lora_base", "lora_world_model_from_full" 등 adapter dir suffix.
local_merged_epoch_dir() {
  local stage="$1" model_short="$2" ds="$3" variant_key="$4" epoch="$5"
  case "$stage" in
    stage1) printf '%s/outputs/%s/merged/%s_stage1_%s/epoch-%s' \
              "$BASE_DIR" "$ds" "$model_short" "$variant_key" "$epoch" ;;
    stage2) printf '%s/outputs/%s/merged/%s_stage2_%s/epoch-%s' \
              "$BASE_DIR" "$ds" "$model_short" "$variant_key" "$epoch" ;;
    *) echo "[!] local_merged_epoch_dir: unknown stage '$stage'" >&2; return 1 ;;
  esac
}
