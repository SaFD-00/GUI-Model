# GUI-Model

모바일 GUI World Modeling 이 Action Prediction 성능에 미치는 영향을 검증하는 2-stage fine-tuning 파이프라인이다. 현재 코드 기준으로는 notebook 이 전체 실행의 기준이고, `scripts/` 가 반복 실행용 자동화 레이어이며, 실제 학습/평가 엔진은 **모델별 백엔드(LlamaFactory 또는 Unsloth)** 가 담당한다.

- 대부분의 모델(Qwen, LLaVA 계열) → 저장소 내부 [`LlamaFactory/`](./LlamaFactory) 사용
- Gemma-4 계열(E2B/E4B) → 저장소 내부 [`unsloth/`](./unsloth) (https://github.com/unslothai/unsloth) 사용

Backend 는 `scripts/_common.sh` 의 `MODEL_BACKEND` 매핑에 따라 자동 분기되므로, 사용자 인터페이스(`--model MODEL --dataset DS`)는 기존과 동일하다.

## 개요

- Stage 1: `screenshot + UI XML + action -> next UI XML`
- Stage 2: `screenshot + UI XML + task -> action JSON`
- 비교 실험:
  - `Exp-1`: Stage 1 world model 품질 평가
  - `stage2`: base model 에서 바로 Stage 2 LoRA
  - `stage1+stage2`: Stage 1 merged model 위에서 Stage 2 LoRA

실제 파이프라인 로직은 [`gui-model.ipynb`](./gui-model.ipynb), [`scripts/`](./scripts), [`LlamaFactory/`](./LlamaFactory) 조합으로 구성된다. [`gui_model/`](./gui_model) 패키지는 배포용 스텁만 포함한다.

## 지원 모델

| # | model_id | short_name | template | backend |
|---|----------|------------|----------|---------|
| 1 | Qwen/Qwen2-VL-2B-Instruct | qwen2-vl-2b | qwen2_vl | llamafactory |
| 2 | Qwen/Qwen2-VL-7B-Instruct | qwen2-vl-7b | qwen2_vl | llamafactory |
| 3 | Qwen/Qwen2.5-VL-3B-Instruct | qwen2.5-vl-3b | qwen2_vl | llamafactory |
| 4 | Qwen/Qwen2.5-VL-7B-Instruct | qwen2.5-vl-7b | qwen2_vl | llamafactory |
| 5 | Qwen/Qwen3-VL-2B-Instruct | qwen3-vl-2b | qwen3_vl_nothink | llamafactory |
| 6 | Qwen/Qwen3-VL-4B-Instruct | qwen3-vl-4b | qwen3_vl_nothink | llamafactory |
| 7 | Qwen/Qwen3-VL-8B-Instruct | qwen3-vl-8b | qwen3_vl_nothink | llamafactory |
| 8 | unsloth/gemma-4-E2B-it | gemma-4-e2b | gemma4 | **unsloth** |
| 9 | unsloth/gemma-4-E4B-it | gemma-4-e4b | gemma4 | **unsloth** |
| 10 | llava-hf/llava-v1.6-mistral-7b-hf | llava-v1.6-mistral-7b | llava_next | llamafactory |
| 11 | llava-hf/llava-v1.6-vicuna-7b-hf | llava-v1.6-vicuna-7b | llava_next | llamafactory |
| 12 | llava-hf/llama3-llava-next-8b-hf | llama3-llava-next-8b | llava_next | llamafactory |

## 디렉토리 구조

```
GUI-Model/
├── gui-model.ipynb
├── scripts/
│   ├── _common.sh                # 공통 path/모델 레지스트리/backend 매핑
│   ├── stage{1,2}_{train,eval,merge}.sh
│   ├── _unsloth_train.py         # Unsloth 학습 entrypoint (Gemma-4)
│   ├── _unsloth_merge.py         # Unsloth merge entrypoint
│   ├── _hungarian_eval.py        # Stage 1 metric
│   ├── _action_eval.py           # Stage 2 metric
│   └── split_data.py
├── data/
├── LlamaFactory/                 # backend=llamafactory (clone)
│   ├── examples/custom/          # LlamaFactory YAML (노트북이 생성)
│   └── scripts/vllm_infer.py     # 모든 backend 공통 추론 도구
├── unsloth/                      # backend=unsloth (clone)
│   └── configs/GUI-Model-{MB,AC}/stage{1,2}_*/...   # Unsloth YAML
├── gui_model/                    # 배포용 스텁
├── setup.py
├── .env.example                  # HF_TOKEN, NPROC_PER_NODE
├── README.md
├── ARCHITECTURE.md
└── AGENTS.md
```

## 환경 설치

단일 진입점. `setup.py` 의 `install_requires` 가 `./LlamaFactory` 와 `./unsloth[huggingface,triton]` 을 PEP 508 `file://` direct reference 로 연쇄 설치하고, `accelerate`/`vllm`/`deepspeed`/metrics 패키지를 함께 해결한다. `.env` 로딩용 `python-dotenv` 도 `INSTALL_REQUIRES` 에 포함되어 `pip install -e .` 한 번으로 함께 설치된다. **`transformers==5.5.4` 는 최상위(`pyproject.toml` `[project].dependencies` 와 `setup.py` `INSTALL_REQUIRES`)에서 고정**한다.

```bash
conda activate gui-model
cd /path/to/GUI-Model
PIP_USER=0 pip install --no-user -e .
```

`PIP_USER=0` / `--no-user` 는 root 유저 + `PYTHONUSERBASE` 조합에서 pip 가 deps 를 user-site(예: `/root/.local/workspace/python-packages`) 로 흘려 conda env `bin/` 에 `accelerate` 같은 CLI entry point 가 만들어지지 않는 사고를 막는다.

### 전제

- Python 3.10 이상, 3.13 미만
- bash 4+ (`scripts/_common.sh` 기준)
- **`transformers==5.5.4`** (최상위 pin). 서브프로젝트(`unsloth/` `<=5.5.0`, `LlamaFactory/` `<=5.2.0`) 의 transitive 상한과 충돌해 pip resolver 가 실패하면 다음 절차로 회피한다:

  ```bash
  pip install --upgrade transformers==5.5.4
  # 또는 서브프로젝트만 deps 무시 재설치
  pip install -e ./unsloth[huggingface,triton] --no-deps
  pip install -e ./LlamaFactory --no-deps
  ```
- merge/export 단계에서는 `HF_TOKEN` (HF Hub push 용), `pyyaml`
- `./LlamaFactory`, `./unsloth` 는 vendored non-editable 로 설치된다. 서브프로젝트 소스를 수정하며 쓰고 싶으면 아래로 덮어쓴다:

  ```bash
  pip install -e ./LlamaFactory --no-deps
  pip install -e './unsloth[huggingface,triton]' --no-deps
  ```

### Unsloth 학습 시 deepspeed 제거

Unsloth (Gemma-4 e2b/e4b) 백엔드는 deepspeed 와 호환되지 않는다 (FastModel 의 메모리 최적화 / gradient checkpointing 이 deepspeed ZeRO 와 충돌하고, env 에 deepspeed 가 남아 있으면 `accelerate launch` 가 deepspeed plugin 을 자동 활성화해 첫 step 에서 실패할 수 있다). 노트북 [`gui-model.ipynb`](./gui-model.ipynb) 의 Cell 5 (`%%bash` + `pip uninstall -y deepspeed`) 가 학습 직전 deepspeed 를 env 에서 제거한다. LlamaFactory 백엔드 모델 학습으로 돌아갈 때는 다음 중 하나로 deepspeed 를 복원한다:

```bash
pip install -e .   # setup.py 의 deepspeed>=0.10.0,<=0.18.4 재설치
# 또는
pip install 'deepspeed>=0.10.0,<=0.18.4'
```

### PATH / user-site 정책

`scripts/_common.sh` 는 conda env 가 활성화돼 있을 때 `$CONDA_PREFIX/bin` 을 PATH 최상단에 고정한다. user-site (`PYTHONUSERBASE/.../bin`) 에 낡은 `accelerate` CLI 가 남아 있어도 env 소속 CLI 가 먼저 잡히도록 보장하기 위함이며, user-site 자체는 유지한다 (일부 deps 가 거기 설치된 상태인 환경을 고려).

conda env 미활성 상태에서 `scripts/*.sh` 를 실행하면 스크립트가 바로 중단된다. 반드시 `conda activate gui-model` 먼저.

### 깨진 env 복구

user-site 에 손상된 패키지가 섞여 `pip install -e .` 가 "already satisfied" 로 넘어가거나 CLI shebang 이 base env python 을 가리키는 상태라면:

```bash
# 문제 패키지만 env 에 강제 재설치 (예: accelerate)
PIP_USER=0 pip install --no-user --force-reinstall --no-deps "accelerate>=1.3.0,<=1.11.0"

# 또는 전체 재해석
PIP_USER=0 pip install --no-user --force-reinstall --no-deps -e .
```

검증:

```bash
which accelerate                 # → $CONDA_PREFIX/bin/accelerate
head -1 "$(which accelerate)"    # → #!$CONDA_PREFIX/bin/python...
python -c "import accelerate, torch; print(accelerate.__file__, torch.__version__)"
```

## 데이터 준비

학습 대상 DS 는 **AndroidControl (AC)** 과 **MonkeyCollection (MC)** 두 가지. **MobiBench (MB)** 는 평가 전용 벤치마크이므로 split 하지 않고 단일 파일 두 개만 둔다.

```
data/
├── AndroidControl/                 # 학습 + 평가
│   ├── gui-model_stage1.jsonl
│   ├── gui-model_stage2.jsonl
│   └── images/
├── MonkeyCollection/                # Stage 1 학습 + 평가 (Stage 2 없음)
│   ├── gui-model_stage1.jsonl       # 약 100K
│   └── images/
└── MobiBench/                       # 평가 전용 벤치마크 (단일 파일)
    ├── gui-model_stage1.jsonl
    ├── gui-model_stage2.jsonl
    └── images/
```

AC 는 Stage 2 에서 **앱 도메인 일반화** 평가를 위해 **in-domain / out-of-domain** test 로 분리한다. MC 는 Stage 2 데이터가 없으므로 Stage 1 random split 만 수행한다. MB 는 split 하지 않는다.

```bash
# 1) AC: 에피소드 메타데이터 (primary_app) 추출 — Stage 2 ID/OOD split 의 입력.
python scripts/extract_androidcontrol_metadata.py \
    --output data/AndroidControl/episodes_meta.jsonl

# 2) AC: Stage 1 random split + Stage 2 ID/OOD split.
python scripts/split_data.py --dataset AndroidControl      # 기본: train 50K / test_id 3K / test_ood 3K

# 3) MC: Stage 1 random split 만 (Stage 2 는 자동 skip).
python scripts/split_data.py --dataset MonkeyCollection

# 4) MB: split 불필요. data/MobiBench/gui-model_stage{1,2}.jsonl 만 있으면 평가 가능.
```

분할 규칙:

- **Stage 1 (AC, MC)**: random split (`--stage1-ratio`, 기본 0.95).
- **Stage 2 (AC only)**: app-level **ID/OOD** split.
  - 앱 집합을 셔플 → OOD 버킷이 `--stage2-test-ood-size` 행 수를 채울 때까지 먼저 할당, 나머지는 ID 버킷.
  - ID 풀에서 `test_id_size` 행을 action-type stratified 로 샘플 → 나머지 + null-app 행을 합쳐 `train_size` 로 stratified subsample (largest-remainder).
  - OOD 풀에서 `test_ood_size` 행을 action-type stratified 로 샘플.
  - `primary_app` 은 첫 `open_app` action 의 `app_name` (AC 메타 추출기가 생성).
- **MB**: split 없음. `gui-model_stage1.jsonl` 과 `gui-model_stage2.jsonl` 각각이 평가 세트 전체. `_action_eval.py` 는 single-pair overall 모드로 채점.

산출물 (학습 DS):

```
data/{AndroidControl,MonkeyCollection}/
├── gui-model_stage1.jsonl
├── gui-model_stage1_train.jsonl     # 95%
├── gui-model_stage1_test.jsonl      # 5%
└── (AC only) episodes_meta.jsonl, gui-model_stage2_{train,test_id,test_ood}.jsonl
```

## 실행 방법

### 1. notebook 경로

[`gui-model.ipynb`](./gui-model.ipynb) 의 섹션 순서대로 실행한다.

> **Global batch size 자동 계산**: Section 0 (Cell 6) 이 `.env` 의 `NPROC_PER_NODE` 와
> 프레임워크별 `per_device_train_batch_size` (LF=2, Unsloth=1) 로부터 `gradient_accumulation_steps`
> 를 역계산해 `GLOBAL_BATCH_SIZE=64` 를 유지한다. GPU 수가 바뀌면 `.env` 의 `NPROC_PER_NODE`
> 만 수정하고 노트북 Cell 6 과 YAML 생성 셀(9/11/15/17/61) 을 다시 실행하면 된다.
>
> **AC 하이퍼파라미터는 모델 크기 3 단(2B / 3-4B / 7-8B) 으로 공유**된다 (`_SIZE_CONFIG_AC`).
> 상세 tier 표와 계열 delta 규칙은 [`ARCHITECTURE.md`](./ARCHITECTURE.md) §2 "하이퍼파라미터" 참조.
> MB 는 tier 미적용 — dataset baseline + per-model override 구조 그대로.

1. Section 0: 환경 설정, dataset config, 모델 config (`_MODEL_CONFIG`), YAML 생성 (Stage 1 full · lora 학습용 + Stage 2 base · world-model-full · world-model-lora 학습용 · merge 용). **Stage 1 eval YAML 은 더 이상 생성하지 않는다** (쉘 스크립트가 HF Hub merged 모델을 직접 sweep).
2. Section 1-2: `dataset_info.json` 등록
3. Section 3: Stage 1 학습 (노트북 셀은 default=full. LoRA 는 쉘 스크립트 `--stage1-mode lora`)
4. Section 4: **Stage 1 merge (모든 epoch 을 각각 merge + HF Hub push)**
5. Section 5: **Stage 1 평가 (HF Hub 에서 `--epochs` 지정 merged 모델 pull). winner 자동 선정 없음 — 사용자가 결과를 보고 Stage 2 에 사용할 epoch 을 수동 결정.**
6. Section 6: Stage 2 학습 (world-model variant 는 `--stage1-epoch N` 으로 상류 Stage 1 epoch 의 local merged 를 base 로 사용)
7. Section 7: **Stage 2 merge (variant × 모든 epoch 각각 merge + HF Hub push)**
8. Section 8: **Stage 2 평가 (HF Hub pull sweep, ID+OOD 동시. `action_metrics.json` 에 overall/in_domain/out_of_domain 3 섹션)**

### 2. shell script 경로

shell script 는 notebook 으로 한 번 생성된 **학습/merge YAML** 과 `LlamaFactory/data/dataset_info.json` 이 이미 있다는 전제에서 동작한다. **Stage 1 eval 은 YAML 을 사용하지 않고 쉘 스크립트가 직접 HF Hub merged 모델을 sweep 한다.**

Stage 1 은 `--stage1-mode full|lora` (기본 `full`), Stage 2 는 `--stage2-mode full|lora` (기본 `lora`) 로 finetuning 방식을 선택한다. world-model variant 학습·머지·평가는 `--stage1-epoch N` 으로 사용할 Stage 1 epoch 을 직접 지정한다 (자동 winner 선정은 없다).

```bash
# Stage 1 Full FT — train → merge → eval (AC 학습, AC/MC/MB 교차 평가)
bash scripts/stage1_train.sh --model qwen3-vl-8b --dataset AC
bash scripts/stage1_merge.sh --model qwen3-vl-8b --dataset AC                                # 모든 epoch push
bash scripts/stage1_eval.sh  --model qwen3-vl-8b --train-dataset AC --eval-datasets AC,MC,MB \
     --variants base,full_world_model --epochs 1,2,3                                         # HF Hub sweep

# Stage 1 LoRA (Gemma-4 예시) — MC 학습
bash scripts/stage1_train.sh --model gemma-4-e2b --dataset MC --stage1-mode lora
bash scripts/stage1_merge.sh --model gemma-4-e2b --dataset MC --stage1-mode lora
bash scripts/stage1_eval.sh  --model gemma-4-e2b --train-dataset MC --eval-datasets AC,MC,MB \
     --stage1-mode lora --variants base,lora_world_model --epochs 1,2,3

# Stage 2 — AC 전용 (MC 는 Stage 2 데이터 없음). AC 학습 모델을 AC + MB 에서 평가.
bash scripts/stage2_train.sh --model qwen3-vl-8b --dataset AC \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora
bash scripts/stage2_merge.sh --model qwen3-vl-8b --dataset AC \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora
bash scripts/stage2_eval.sh  --model qwen3-vl-8b --train-dataset AC --eval-datasets AC,MB \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora \
     --variants base,lora_base,lora_world_model --epochs 1,2,3

# Stage 2 Full FT
bash scripts/stage2_train.sh --model qwen3-vl-8b --dataset AC \
     --stage1-mode full --stage1-epoch 3 --stage2-mode full
```

> `--epochs` 생략 시 기본 `1,2,3` 이 적용된다.
> `--variants` 생략 시 `stage1_eval.sh` 는 `base,full_world_model,lora_world_model`, `stage2_eval.sh` 는 `base,full_base,lora_base,full_world_model,lora_world_model` 전체를 돈다.

> **재실행 시 skip**: `stage{1,2}_eval.sh` 는 각 unit 의 marker 파일 (`hungarian_metrics.json` / `action_metrics.json`) 이 이미 존재하면 건너뛰고 `[=] ... skip (already done): ...` 로그만 남긴다. 강제 재평가는 해당 파일을 `rm` 후 재실행.

지원 플래그 (전체):

**학습/merge 스크립트 (`stage{1,2}_{train,merge}.sh`)**:
- `--model MODEL`: 모델 short_name 또는 `all` (기본: `all`)
- `--dataset DS`: `AC` | `MC` | `all` (기본: `all`) — 학습 대상 DS. **MB 는 평가 전용이므로 거절**.
- `--stage1-mode MODE`: `full` | `lora` (기본: `full`) — Stage 1 학습/merge 방식 및 world-model 상류 소스
- `--stage2-mode MODE`: `full` | `lora` (기본: `lora`) — Stage 2 학습/merge 방식 (stage2 스크립트 전용)
- `--stage1-epoch N`: Stage 2 world-model variant 에서 참조할 상류 Stage 1 epoch (stage2 전용)

**평가 스크립트 (`stage{1,2}_eval.sh`)**: 학습 DS 와 평가 DS 를 분리.
- `--model MODEL`
- `--train-dataset DS`: `AC` | `MC` (필수) — HF Hub merged repo 를 식별할 학습 DS. Stage 2 eval 은 현재 AC 만.
- `--eval-datasets LIST`: 콤마 구분 (기본: `--train-dataset` 단일값). 허용값 `AC,MC,MB`.
  - AC/MC: 기존 `*_test.jsonl` (Stage 2 는 `*_test_{id,ood}.jsonl`).
  - MB: 단일 파일 `gui-model_stage{1,2}.jsonl`, Stage 2 는 single-pair overall 1-섹션 채점.
- `--stage1-mode`, `--stage2-mode`, `--stage1-epoch` (상동)
- `--epochs LIST`: 콤마 구분 정수 (기본 `1,2,3`) — HF Hub sweep 대상 epoch.
- `--variants LIST`: 콤마 구분 평가 변형 목록.

주요 동작:

- `stage1_merge.sh` 는 모든 `checkpoint-*/` 를 돌면서 `trainer_state.json.epoch` 기반으로 `merged/{MODEL}_stage1_${MODE}/epoch-{E}/` 로 local merge + HF 에 `SaFD-00/{short}-{slug}world-model-stage1-{MODE}-epoch{E}` 푸시.
- `stage1_eval.sh` 는 `--variants` + `--epochs` 로 지정된 HF repo 를 pull 해 `hungarian_metrics.json` 을 산출한다. 어떤 epoch 으로 Stage 2 를 할지는 사용자가 결과를 보고 직접 결정한다.
- `stage2_train.sh` 는 `stage2_{full|lora}/{MODEL}_{base,world-model-{full,lora}}.yaml` 을 학습한다. world-model variant 는 `--stage1-epoch N` 으로 지정된 로컬 `merged/{MODEL}_stage1_${STAGE1_MODE}/epoch-${N}/` 를 base 로 사용 (YAML `model_name_or_path` 런타임 sed 치환).
- `stage2_merge.sh` 는 각 epoch 를 merge + HF push:
  - base variant: `SaFD-00/{short}-{slug}base-stage2-{MODE2}-epoch{E2}`
  - world-model: `SaFD-00/{short}-{slug}world-model-stage1-{MODE1}-epoch{E1}-stage2-{MODE2}-epoch{E2}`
- `stage2_eval.sh` 는 ID + OOD 테스트 파일 (`gui-model_stage2_test_{id,ood}.jsonl`) 을 **동시에** 추론하고 `_action_eval.py score` 가 `overall` / `in_domain` / `out_of_domain` 3 섹션을 한 `action_metrics.json` 에 기록한다. Step Accuracy 정의는 `ARCHITECTURE.md` §6 참고.

## 산출물

모든 결과물은 `GUI-Model/outputs/` 단일 루트 아래에 **데이터셋 중심** 으로 모인다. `adapters/`·`merged/` 는 flat 네이밍 `{MODEL}_{detail}/`, `eval/` 만 중첩 구조를 유지한다. Stage 1 full/lora 산출물은 경로 접미사로 분리되어 공존 가능하다.

```
GUI-Model/outputs/{MB|AC}/
├── adapters/
│   ├── {model}_stage1_{full,lora}/                                       # Stage 1 체크포인트 (epoch 단위 저장)
│   ├── {model}_stage2_{full,lora}_base/                                  # Stage 2 base (finetuning_type=full|lora)
│   └── {model}_stage2_{full,lora}_world_model_from_{full,lora}/          # Stage 2 world-model (Stage2 mode × Stage1 mode)
├── eval/{model}/
│   ├── stage1_eval/{base, {full,lora}_world_model/epoch-{E}}/            # generated_predictions, hungarian_metrics
│   └── stage2_eval/{base,
│                     {full,lora}_base/epoch-{E},
│                     {full,lora}_world_model_from_{full,lora}_ep{E1}/epoch-{E2}}/
│                                                                         # generated_predictions_{id,ood}, action_metrics (overall+id+ood)
└── merged/
    ├── {model}_stage1_{full,lora}/epoch-{E}/
    ├── {model}_stage2_{full,lora}_base/epoch-{E}/
    └── {model}_stage2_{full,lora}_world_model_from_{full,lora}/epoch-{E}/
```

HF Hub 레포지토리 네이밍 (epoch 별 개별 repo):

- `SaFD-00/{short}-{slug}world-model-stage1-{full,lora}-epoch{E}`                       (Stage 1)
- `SaFD-00/{short}-{slug}base-stage2-{full,lora}-epoch{E2}`                             (Stage 2 base, Stage 1 무관)
- `SaFD-00/{short}-{slug}world-model-stage1-{M1}-epoch{E1}-stage2-{M2}-epoch{E2}`       (Stage 2 world-model, M1/M2 ∈ {full,lora})

repo id 조립은 `scripts/_common.sh::hf_repo_id_stage1` / `hf_repo_id_stage2_base` / `hf_repo_id_stage2_world_model` 에 단일화되어 있다. `{E}` 는 각 checkpoint 의 `trainer_state.json` 에서 `int(round(epoch))` 로 추출.

### 데이터셋 split 재생성

AC Stage 2 는 앱 도메인 일반화를 측정하기 위해 **in-domain / out-of-domain** 두 test 파일을 사용한다. MC 는 Stage 2 가 없어 Stage 1 random split 만 수행된다. MB 는 평가 전용 벤치마크이므로 split 대상이 아니다.

```bash
# 1) AC: 메타데이터 (앱 분류용) 생성 — primary_app 필드 포함
python scripts/extract_androidcontrol_metadata.py --output data/AndroidControl/episodes_meta.jsonl

# 2) AC: Stage 1 random split + Stage 2 ID/OOD split
python scripts/split_data.py --dataset AndroidControl   # 기본: train 50K / test_id 3K / test_ood 3K

# 3) MC: Stage 1 random split (Stage 2 자동 skip — _STAGE1_ONLY)
python scripts/split_data.py --dataset MonkeyCollection
```

## 모델 추가 방법

새 모델 추가 시 아래를 동기화해야 한다:

1. `gui-model.ipynb` Cell 3 의 `_MODEL_CONFIG` 딕셔너리에 모델 항목 추가 (`backend` 필드 포함)
2. `scripts/_common.sh` 의 `MODEL_ID`, `MODEL_TEMPLATE`, `ALL_MODELS` 에 동일 항목 추가
3. backend 가 기본값(`llamafactory`)이 아니면 `_common.sh` 의 `MODEL_BACKEND` 매핑에 등록
4. `backend=unsloth` 일 경우 `unsloth/configs/GUI-Model-{AC,MC}/stage{1,2}_*/...` YAML 은 notebook 의 "Unsloth Stage {1,2} YAML 일괄 생성" 셀에서 자동 생성된다 (Gemma-4 e2b/e4b 적용). 동일 모델이 LF `examples/custom/GUI-Model-{AC,MC}/stage{1,2}_*/` 아래에는 생성되지 않는다 (LF 생성 셀들이 `backend == "unsloth"` 을 스킵함). MC 는 Stage 1 전용이므로 `stage2_*/` YAML 은 MC 에 대해 생성되지 않는다 (`_STAGE1_ONLY` guard).

## 테스트 실행

Stage 2 Step Accuracy 채점 로직 (`scripts/_action_eval.py`) 회귀 테스트:

```bash
cd GUI-Model
python -m unittest tests.test_action_eval -v
# 또는 pytest
pytest tests/test_action_eval.py -v
```

현재 48 케이스 — `parse_action` / `evaluate_single` / `evaluate_predictions` 의 주요 분기, unknown type 집계, `cond_*` n=0, `predict`/`output` fallback, ID+OOD 통합 집계를 커버한다. 메트릭 정의는 [`ARCHITECTURE.md`](./ARCHITECTURE.md) §6.

## 코드 읽기 시작점

- [`gui-model.ipynb`](./gui-model.ipynb): 전체 파이프라인 기준
- [`scripts/_common.sh`](./scripts/_common.sh): path, dataset, model, logging 규약
- [`scripts/split_data.py`](./scripts/split_data.py): split 규칙
- [`scripts/_hungarian_eval.py`](./scripts/_hungarian_eval.py): Stage 1 metric
- [`scripts/_action_eval.py`](./scripts/_action_eval.py): Stage 2 metric

구조 설명은 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 작업 규칙은 [`AGENTS.md`](./AGENTS.md) 를 본다.
