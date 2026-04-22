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
# env 소속 CLI (/root/anaconda3/envs/gui-model-{llamafactory,unsloth}/bin/accelerate 등) 가
# 먼저 잡히도록 강제한다.
if [[ -n "${CONDA_PREFIX:-}" ]]; then
  export PATH="$CONDA_PREFIX/bin:$PATH"
else
  echo "[!] conda env 가 활성화되어 있지 않습니다. 모델 backend 에 맞춰 다음 중 하나를 먼저 실행하세요:" >&2
  echo "      conda activate gui-model-llamafactory   # qwen*, llava*" >&2
  echo "      conda activate gui-model-unsloth        # gemma-4-*" >&2
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

# --- dataset prefix / HF slug / data dir 매핑 (Cell 3 _DATASET_CONFIG 와 일치) -
# MB 는 평가 전용 벤치마크(학습 파이프라인 미사용). 학습 대상 DS 는 {AC, MC}.
# MB entry 는 평가 스크립트가 dataset_info 이름/slug 를 조합하는 데 사용.
declare -A DS_PREFIX=(  [MB]="GUI-Model-MB" [AC]="GUI-Model-AC" [MC]="GUI-Model-MC" )
declare -A HF_SLUG=(    [MB]="mb-"          [AC]="ac-"          [MC]="mc-"          )
declare -A DS_DATADIR=( [MB]="MobiBench"    [AC]="AndroidControl" [MC]="MonkeyCollection" )

# --- 모델 레지스트리 (Cell 3 _MODEL_CONFIG 와 일치) ---------------------------
declare -A MODEL_ID=(
  [qwen2-vl-2b]="Qwen/Qwen2-VL-2B-Instruct"
  [qwen2-vl-7b]="Qwen/Qwen2-VL-7B-Instruct"
  [qwen2.5-vl-3b]="Qwen/Qwen2.5-VL-3B-Instruct"
  [qwen2.5-vl-7b]="Qwen/Qwen2.5-VL-7B-Instruct"
  [qwen3-vl-2b]="Qwen/Qwen3-VL-2B-Instruct"
  [qwen3-vl-4b]="Qwen/Qwen3-VL-4B-Instruct"
  [qwen3-vl-8b]="Qwen/Qwen3-VL-8B-Instruct"
  [gemma-4-e2b]="google/gemma-4-E2B-it"
  [gemma-4-e4b]="google/gemma-4-E4B-it"
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

# --- CLI 인자 파싱 (학습/merge 스크립트용): --model MODEL --dataset DS --------
# 사용법:
#   bash script.sh --model qwen3-vl-8b --dataset AC
#   bash script.sh --model qwen3-vl-8b --stage1-mode lora
#   bash script.sh --stage1-mode lora           # 전체 모델 LoRA 학습/평가/merge
#   bash script.sh                               # 기본값: 전체 모델 + 전체 학습 DS + full
#
# 학습 대상 DS 는 {AC, MC} 만. MobiBench(MB) 는 평가 전용 벤치마크이므로
# --dataset MB 입력은 거절된다. 교차 평가는 stage{1,2}_eval.sh 가 제공하는
# parse_eval_args (--train-dataset / --eval-datasets) 를 사용한다.
parse_args() {
  local model_arg="all"
  local dataset_arg="all"
  local stage1_mode_arg="full"
  local stage2_mode_arg="lora"
  local stage1_epoch_arg=""
  local epochs_arg="1,2,3"
  local variants_arg=""
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
      --stage2-mode)
        if [[ -z "${2:-}" ]]; then echo "Error: --stage2-mode requires a value." >&2; exit 2; fi
        stage2_mode_arg="$2"; shift 2 ;;
      --stage1-epoch)
        if [[ -z "${2:-}" ]]; then echo "Error: --stage1-epoch requires a value." >&2; exit 2; fi
        stage1_epoch_arg="$2"; shift 2 ;;
      --epochs)
        if [[ -z "${2:-}" ]]; then echo "Error: --epochs requires a value." >&2; exit 2; fi
        epochs_arg="$2"; shift 2 ;;
      --variants)
        if [[ -z "${2:-}" ]]; then echo "Error: --variants requires a value." >&2; exit 2; fi
        variants_arg="$2"; shift 2 ;;
      -h|--help)
        cat <<EOF
Usage: $(basename "$0") [--model MODEL] [--dataset DS] [--stage1-mode MODE]
                         [--stage2-mode MODE] [--stage1-epoch N] [--epochs LIST]
                         [--variants LIST]

Options:
  --model MODEL        모델 short_name 또는 "all" (기본: all)
  --dataset DS         AC | MC | all (기본: all) — 학습 대상 DS. MB 는 평가 전용이므로 사용 불가.
  --stage1-mode MODE   full | lora (기본: full) — Stage 1 학습 방식.
  --stage2-mode MODE   full | lora (기본: lora) — Stage 2 학습 방식 (Stage 2 전용).
  --stage1-epoch N     Stage 2 world-model variant 가 상류 base 로 삼을 Stage 1 epoch.
                       stage2_{train,merge,eval}.sh 전용.
  --epochs LIST        콤마로 구분된 epoch 정수 리스트 (기본: 1,2,3)
                       stage{1,2}_eval.sh 에서 HF Hub merged repo sweep 대상.
  --variants LIST      콤마로 구분된 변형 목록. stage{1,2}_eval.sh 전용.
                       Stage1: base, full_world_model, lora_world_model
                       Stage2: base, full_base, lora_base, full_world_model, lora_world_model
  -h, --help           이 도움말 표시

Available models:
  ${ALL_MODELS[*]}
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
    *) echo "Error: --stage1-mode must be full | lora (got '$stage1_mode_arg')." >&2; exit 2 ;;
  esac
  case "$stage2_mode_arg" in
    full|lora) STAGE2_MODE="$stage2_mode_arg" ;;
    *) echo "Error: --stage2-mode must be full | lora (got '$stage2_mode_arg')." >&2; exit 2 ;;
  esac

  STAGE1_EPOCH=""
  if [[ -n "$stage1_epoch_arg" ]]; then
    if ! [[ "$stage1_epoch_arg" =~ ^[0-9]+$ ]]; then
      echo "Error: --stage1-epoch must be a positive integer (got '$stage1_epoch_arg')." >&2
      exit 2
    fi
    STAGE1_EPOCH="$stage1_epoch_arg"
  fi

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

  case "$dataset_arg" in
    AC)  DATASETS=(AC) ;;
    MC)  DATASETS=(MC) ;;
    all) DATASETS=(AC MC) ;;
    MB)
      echo "Error: MobiBench (MB) 는 평가 전용 벤치마크입니다. 학습/merge 에는 사용할 수 없습니다." >&2
      echo "       교차 평가는 stage{1,2}_eval.sh --train-dataset {AC|MC} --eval-datasets AC,MC,MB 를 사용하세요." >&2
      exit 2
      ;;
    *) echo "Error: Unknown dataset '$dataset_arg'. Use AC | MC | all." >&2; exit 2 ;;
  esac

  IFS=',' read -r -a EPOCHS <<< "$epochs_arg"
  if [[ "${#EPOCHS[@]}" -eq 0 ]]; then
    echo "Error: --epochs 값이 비어있습니다." >&2; exit 2
  fi
  for _e in "${EPOCHS[@]}"; do
    if ! [[ "$_e" =~ ^[0-9]+$ ]]; then
      echo "Error: --epochs 는 콤마로 구분된 정수여야 합니다 (got: '$epochs_arg')." >&2
      exit 2
    fi
  done
  unset _e

  VARIANTS=()
  if [[ -n "$variants_arg" ]]; then
    IFS=',' read -r -a VARIANTS <<< "$variants_arg"
  fi
}

# --- CLI 인자 파싱 (eval 스크립트용): --train-dataset / --eval-datasets --------
# 학습 DS (HF Hub merged repo 식별용) 와 평가 DS (test JSONL 경로) 를 분리한다.
#
# 사용법:
#   bash stage1_eval.sh --model qwen3-vl-8b --train-dataset AC --eval-datasets AC,MC,MB
#   bash stage2_eval.sh --model qwen3-vl-8b --train-dataset AC --eval-datasets AC,MB \
#        --stage1-mode full --stage1-epoch 3 --stage2-mode lora
#
# 생성 변수:
#   MODELS          ALL_MODELS 또는 단일 모델 배열
#   TRAIN_DATASET   AC | MC  (필수, 단일)
#   EVAL_DATASETS   (AC|MC|MB)+ 배열  (기본: 단일값 = TRAIN_DATASET)
#   STAGE1_MODE, STAGE2_MODE, STAGE1_EPOCH, EPOCHS, VARIANTS  (parse_args 와 동일)
parse_eval_args() {
  local model_arg="all"
  local train_arg=""
  local eval_arg=""
  local stage1_mode_arg="full"
  local stage2_mode_arg="lora"
  local stage1_epoch_arg=""
  local epochs_arg="1,2,3"
  local variants_arg=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --model)
        if [[ -z "${2:-}" ]]; then echo "Error: --model requires a value." >&2; exit 2; fi
        model_arg="$2"; shift 2 ;;
      --train-dataset)
        if [[ -z "${2:-}" ]]; then echo "Error: --train-dataset requires a value." >&2; exit 2; fi
        train_arg="$2"; shift 2 ;;
      --eval-datasets)
        if [[ -z "${2:-}" ]]; then echo "Error: --eval-datasets requires a value." >&2; exit 2; fi
        eval_arg="$2"; shift 2 ;;
      --stage1-mode)
        if [[ -z "${2:-}" ]]; then echo "Error: --stage1-mode requires a value." >&2; exit 2; fi
        stage1_mode_arg="$2"; shift 2 ;;
      --stage2-mode)
        if [[ -z "${2:-}" ]]; then echo "Error: --stage2-mode requires a value." >&2; exit 2; fi
        stage2_mode_arg="$2"; shift 2 ;;
      --stage1-epoch)
        if [[ -z "${2:-}" ]]; then echo "Error: --stage1-epoch requires a value." >&2; exit 2; fi
        stage1_epoch_arg="$2"; shift 2 ;;
      --epochs)
        if [[ -z "${2:-}" ]]; then echo "Error: --epochs requires a value." >&2; exit 2; fi
        epochs_arg="$2"; shift 2 ;;
      --variants)
        if [[ -z "${2:-}" ]]; then echo "Error: --variants requires a value." >&2; exit 2; fi
        variants_arg="$2"; shift 2 ;;
      -h|--help)
        cat <<EOF
Usage: $(basename "$0") --train-dataset {AC|MC} [--eval-datasets LIST] [--model MODEL]
                         [--stage1-mode MODE] [--stage2-mode MODE] [--stage1-epoch N]
                         [--epochs LIST] [--variants LIST]

Options:
  --model MODEL           모델 short_name 또는 "all" (기본: all)
  --train-dataset DS      AC | MC (필수) — HF Hub merged repo 를 해석할 학습 DS
  --eval-datasets LIST    콤마로 구분된 평가 DS 리스트 (기본: --train-dataset 단일값)
                          허용값: AC, MC, MB (MB 는 단일 파일 overall 채점)
  --stage1-mode MODE      full | lora (기본: full) — world-model variant 의 상류 Stage1 모드.
  --stage2-mode MODE      full | lora (기본: lora) — Stage 2 merge/eval 전용.
  --stage1-epoch N        Stage 2 world-model variant 의 HF repo 계보 번호.
  --epochs LIST           콤마 구분 정수 리스트 (기본: 1,2,3) — sweep 대상 epoch.
  --variants LIST         콤마 구분 평가 변형 목록.
                          Stage1: base, full_world_model, lora_world_model
                          Stage2: base, full_base, lora_base, full_world_model, lora_world_model
  -h, --help              이 도움말 표시

Available models:
  ${ALL_MODELS[*]}
EOF
        exit 0
        ;;
      *)
        echo "Error: Unknown argument '$1'. Use --help for usage." >&2
        exit 2
        ;;
    esac
  done

  if [[ -z "$train_arg" ]]; then
    echo "Error: --train-dataset 는 필수입니다 (AC | MC)." >&2; exit 2
  fi
  case "$train_arg" in
    AC|MC) TRAIN_DATASET="$train_arg" ;;
    MB)
      echo "Error: --train-dataset MB 는 허용되지 않습니다 (MobiBench 는 평가 전용)." >&2
      exit 2 ;;
    *) echo "Error: --train-dataset must be AC | MC (got '$train_arg')." >&2; exit 2 ;;
  esac

  if [[ -z "$eval_arg" ]]; then
    EVAL_DATASETS=("$TRAIN_DATASET")
  else
    IFS=',' read -r -a EVAL_DATASETS <<< "$eval_arg"
    if [[ "${#EVAL_DATASETS[@]}" -eq 0 ]]; then
      echo "Error: --eval-datasets 값이 비어있습니다." >&2; exit 2
    fi
    for _d in "${EVAL_DATASETS[@]}"; do
      case "$_d" in
        AC|MC|MB) ;;
        *) echo "Error: --eval-datasets item '$_d' invalid (use AC | MC | MB)." >&2; exit 2 ;;
      esac
    done
    unset _d
  fi

  case "$stage1_mode_arg" in
    full|lora) STAGE1_MODE="$stage1_mode_arg" ;;
    *) echo "Error: --stage1-mode must be full | lora (got '$stage1_mode_arg')." >&2; exit 2 ;;
  esac
  case "$stage2_mode_arg" in
    full|lora) STAGE2_MODE="$stage2_mode_arg" ;;
    *) echo "Error: --stage2-mode must be full | lora (got '$stage2_mode_arg')." >&2; exit 2 ;;
  esac

  STAGE1_EPOCH=""
  if [[ -n "$stage1_epoch_arg" ]]; then
    if ! [[ "$stage1_epoch_arg" =~ ^[0-9]+$ ]]; then
      echo "Error: --stage1-epoch must be a positive integer (got '$stage1_epoch_arg')." >&2
      exit 2
    fi
    STAGE1_EPOCH="$stage1_epoch_arg"
  fi

  if [[ "$model_arg" == "all" ]]; then
    MODELS=("${ALL_MODELS[@]}")
  elif [[ -n "${MODEL_ID[$model_arg]+x}" ]]; then
    MODELS=("$model_arg")
  else
    echo "Error: Unknown model '$model_arg'." >&2
    echo "Available: ${ALL_MODELS[*]} | all" >&2
    exit 2
  fi

  IFS=',' read -r -a EPOCHS <<< "$epochs_arg"
  if [[ "${#EPOCHS[@]}" -eq 0 ]]; then
    echo "Error: --epochs 값이 비어있습니다." >&2; exit 2
  fi
  for _e in "${EPOCHS[@]}"; do
    if ! [[ "$_e" =~ ^[0-9]+$ ]]; then
      echo "Error: --epochs 는 콤마로 구분된 정수여야 합니다 (got: '$epochs_arg')." >&2
      exit 2
    fi
  done
  unset _e

  VARIANTS=()
  if [[ -n "$variants_arg" ]]; then
    IFS=',' read -r -a VARIANTS <<< "$variants_arg"
  fi
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

# --- skip-if-exists 가드 -----------------------------------------------------
# usage: if skip_if_done <tag> <marker>; then continue; fi
# marker 파일이 이미 존재하면 stderr 에 skip 메시지를 찍고 0 (success) 을 반환.
# 호출부에서 `continue` / `:` 로 우회하는 패턴으로 사용한다.
skip_if_done() {
  local tag="$1" marker="$2"
  if [ -f "$marker" ]; then
    echo "[=] [$tag] skip (already done): $marker" >&2
    return 0
  fi
  return 1
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
# Stage 1: SaFD-00/{short}-{slug}world-model-stage1-{mode}-epoch{E}
#   ex: SaFD-00/qwen2.5-vl-7b-ac-world-model-stage1-full-epoch1
hf_repo_id_stage1() {
  local model_short="$1" ds="$2" mode="$3" epoch="$4"
  printf 'SaFD-00/%s-%sworld-model-stage1-%s-epoch%s' \
    "$model_short" "${HF_SLUG[$ds]}" "$mode" "$epoch"
}

# Stage 2 (base variant):
#   SaFD-00/{short}-{slug}base-stage2-{mode2}-epoch{E2}
#   ex: SaFD-00/qwen2.5-vl-7b-ac-base-stage2-full-epoch1
hf_repo_id_stage2_base() {
  local model_short="$1" ds="$2" mode2="$3" epoch2="$4"
  printf 'SaFD-00/%s-%sbase-stage2-%s-epoch%s' \
    "$model_short" "${HF_SLUG[$ds]}" "$mode2" "$epoch2"
}

# Stage 2 (world-model variant — Stage 1 계보 포함):
#   SaFD-00/{short}-{slug}world-model-stage1-{mode1}-epoch{E1}-stage2-{mode2}-epoch{E2}
#   ex: SaFD-00/qwen2.5-vl-7b-ac-world-model-stage1-full-epoch3-stage2-lora-epoch1
hf_repo_id_stage2_world_model() {
  local model_short="$1" ds="$2" mode1="$3" epoch1="$4" mode2="$5" epoch2="$6"
  printf 'SaFD-00/%s-%sworld-model-stage1-%s-epoch%s-stage2-%s-epoch%s' \
    "$model_short" "${HF_SLUG[$ds]}" "$mode1" "$epoch1" "$mode2" "$epoch2"
}

# --- Local merged 디렉토리 경로 ---------------------------------------------
# stage1: merged/{MODEL}_stage1_{MODE}/epoch-{E}
# stage2: merged/{MODEL}_stage2_{variant_key}/epoch-{E}
#   variant_key 예: "{full|lora}_base", "{full|lora}_world_model_from_{full|lora}".
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

# --- Variant 유효성 체크 + 기본값 ---------------------------------------------
# Stage 1 변형: base, full_world_model, lora_world_model
STAGE1_ALL_VARIANTS=(base full_world_model lora_world_model)
# Stage 2 변형: base, full_base, lora_base, full_world_model, lora_world_model
STAGE2_ALL_VARIANTS=(base full_base lora_base full_world_model lora_world_model)

# Stage 1 variants 를 지정하지 않았으면 전체를 사용. 잘못된 항목은 error.
resolve_stage1_variants() {
  if [[ "${#VARIANTS[@]}" -eq 0 ]]; then
    VARIANTS=("${STAGE1_ALL_VARIANTS[@]}")
    return
  fi
  for v in "${VARIANTS[@]}"; do
    local ok=0
    for allowed in "${STAGE1_ALL_VARIANTS[@]}"; do
      if [[ "$v" == "$allowed" ]]; then ok=1; break; fi
    done
    if (( ok == 0 )); then
      echo "Error: unknown stage1 variant '$v'." >&2
      echo "Allowed: ${STAGE1_ALL_VARIANTS[*]}" >&2
      exit 2
    fi
  done
}

resolve_stage2_variants() {
  if [[ "${#VARIANTS[@]}" -eq 0 ]]; then
    VARIANTS=("${STAGE2_ALL_VARIANTS[@]}")
    return
  fi
  for v in "${VARIANTS[@]}"; do
    local ok=0
    for allowed in "${STAGE2_ALL_VARIANTS[@]}"; do
      if [[ "$v" == "$allowed" ]]; then ok=1; break; fi
    done
    if (( ok == 0 )); then
      echo "Error: unknown stage2 variant '$v'." >&2
      echo "Allowed: ${STAGE2_ALL_VARIANTS[*]}" >&2
      exit 2
    fi
  done
}
