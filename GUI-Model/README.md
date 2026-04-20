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

`data/` 아래에 아래 형태로 원본 JSONL 과 이미지를 둔다.

```
data/
├── MobiBench/
│   ├── gui-model_stage1.jsonl
│   ├── gui-model_stage2.jsonl
│   └── images/
└── AndroidControl/
    ├── gui-model_stage1.jsonl
    ├── gui-model_stage2.jsonl
    └── images/
```

train/test split 생성:

```bash
python scripts/split_data.py --dataset MobiBench
python scripts/split_data.py --dataset AndroidControl
```

현재 코드 기준 분할 규칙:

- Stage 1: random split
- Stage 2: action type 기준 stratified split
- `AndroidControl` Stage 2: `split_data.py` 기본값으로 `30000`개 stratified subsample 후 split

## 실행 방법

### 1. notebook 경로

[`gui-model.ipynb`](./gui-model.ipynb) 의 섹션 순서대로 실행한다.

> **Global batch size 자동 계산**: Section 0 (Cell 6) 이 `.env` 의 `NPROC_PER_NODE` 와
> 프레임워크별 `per_device_train_batch_size` (LF=2, Unsloth=1) 로부터 `gradient_accumulation_steps`
> 를 역계산해 `GLOBAL_BATCH_SIZE=64` 를 유지한다. GPU 수가 바뀌면 `.env` 의 `NPROC_PER_NODE`
> 만 수정하고 노트북 Cell 6 과 YAML 생성 셀(9/11/15/17/61) 을 다시 실행하면 된다.

1. Section 0: 환경 설정, dataset config, 모델 config (`_MODEL_CONFIG`), YAML 생성 (Stage 1 full · lora 학습용 + Stage 2 base · world-model-full · world-model-lora 학습용 · merge 용). **Stage 1 eval YAML 은 더 이상 생성하지 않는다** (쉘 스크립트가 HF Hub merged 모델을 직접 sweep).
2. Section 1-2: `dataset_info.json` 등록
3. Section 3: Stage 1 학습 (노트북 셀은 default=full 기준. LoRA 는 아래 쉘 스크립트 사용)
4. Section 4: **Stage 1 merge (모든 epoch 을 각각 merge + HF Hub push)**
5. Section 5: **Stage 1 평가 (HF Hub 에서 `EPOCHS` 리스트에 지정된 merged 모델 pull) 및 winner 선택**
6. Section 6: Stage 2 학습 (world-model variant 는 상류 Stage 1 winner epoch 의 local merged 를 base 로 사용 — `BEST_CHECKPOINT.json.epoch` 자동 해석)
7. Section 7: **Stage 2 merge (variant × 모든 epoch 각각 merge + HF Hub push)**
8. Section 8: **Stage 2 평가 (HF Hub pull sweep, `EPOCHS` 리스트 기반) 및 winner 선택**

### 2. shell script 경로

shell script 는 notebook 으로 한 번 생성된 **학습/merge YAML** 과 `LlamaFactory/data/dataset_info.json` 이 이미 있다는 전제에서 동작한다. **Stage 1 eval 은 YAML 을 사용하지 않고 쉘 스크립트가 직접 HF Hub merged 모델을 sweep 한다.**

Stage 1 은 `--stage1-mode full|lora` 플래그로 finetuning 방식을 선택한다 (기본: `full`). Stage 2 스크립트도 같은 플래그를 받아 world-model variant 의 상류 Stage 1 소스를 결정한다.

```bash
# Stage 1 Full FT (default) — 표준 순서: train → merge → eval
bash scripts/stage1_train.sh --model qwen3-vl-8b --dataset MB
bash scripts/stage1_merge.sh --model qwen3-vl-8b --dataset MB                     # 모든 epoch push
bash scripts/stage1_eval.sh  --model qwen3-vl-8b --dataset MB --epochs 1,2,3      # HF Hub sweep + winner

# Stage 1 LoRA
bash scripts/stage1_train.sh --model gemma-4-e2b --dataset MB --stage1-mode lora
bash scripts/stage1_merge.sh --model gemma-4-e2b --dataset MB --stage1-mode lora
bash scripts/stage1_eval.sh  --model gemma-4-e2b --dataset MB --stage1-mode lora --epochs 1,2,3

# Stage 2 — world-model variant 가 Stage 1 full winner 를 base 로 사용
bash scripts/stage2_train.sh --model qwen3-vl-8b --dataset MB
bash scripts/stage2_merge.sh --model qwen3-vl-8b --dataset MB
bash scripts/stage2_eval.sh  --model qwen3-vl-8b --dataset MB --epochs 1,2,3

# Stage 2 — world-model variant 가 Stage 1 lora winner 를 base 로 사용
bash scripts/stage2_train.sh --model gemma-4-e2b --dataset MB --stage1-mode lora
bash scripts/stage2_merge.sh --model gemma-4-e2b --dataset MB --stage1-mode lora
bash scripts/stage2_eval.sh  --model gemma-4-e2b --dataset MB --stage1-mode lora --epochs 1,2,3
```

> `--epochs` 생략 시 기본값 `1,2,3` 이 적용된다. 학습한 `num_train_epochs` 와 다른 값을 쓰려면 명시해야 한다 (예: `--epochs 3` 으로 최종 epoch 만 평가).

지원 플래그:

- `--model MODEL`: 모델 short_name 또는 `all` (기본: `all`)
- `--dataset DS`: `MB` | `AC` | `all` (기본: `all`)
- `--stage1-mode MODE`: `full` | `lora` (기본: `full`)
- `--epochs LIST`: 콤마로 구분된 epoch 정수 리스트 (기본: `1,2,3`). `stage{1,2}_eval.sh` 전용 — HF Hub merged repo sweep 대상. 다른 스크립트에서는 parse 만 되고 무시된다.
- `-h`, `--help`: 도움말

주요 동작:

표준 실행 순서는 **train → merge → eval** 다. merge 가 모든 epoch checkpoint 를 각각 local merge + HF Hub push 하고, eval 이 `--epochs` 로 지정된 epoch 리스트만큼 HF Hub 에서 merged 모델을 pull 해서 sweep + winner 선정한다. `HF_TOKEN` 은 merge/eval 모두에 필요하다 (private repo 포함).

- `stage1_merge.sh` 는 `outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/checkpoint-*/` 전수 loop 로 각 ckpt 의 `trainer_state.json` 에서 epoch 을 파싱해 `outputs/{DS}/merged/{MODEL}_stage1_${MODE}/epoch-{E}/` 로 local merge + HF Hub 에 `SaFD-00/{short}-{slug}stage1-{full|lora}-world-model-epoch{E}` 로 push 한다. LF LoRA 모드는 임시 merge YAML 에 `adapter_name_or_path` + `finetuning_type: lora` 블록을 삽입.
- `stage1_eval.sh` 는 (a) baseline zero-shot 과 (b) `--epochs` 리스트 기반 **HF Hub repo sweep** 을 수행한다 (`--model_name_or_path <HF repo id>`. merged 이므로 adapter 인자 불필요, 로컬 checkpoint 디렉토리도 조회하지 않는다).
  - 결과: `outputs/{DS}/eval/{MODEL}/stage1_eval/{base, {full|lora}_world_model/epoch-{E}}/`
  - `avg_hungarian_f1` 기준 winner 를 `outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/BEST_CHECKPOINT[.json]` 에 기록 (JSON 에는 `epoch`, `hf_repo_id` 필드 포함).
- `stage2_train.sh` 는 `{MODEL}_base.yaml` + `{MODEL}_world-model-{full|lora}.yaml` 두 variant 를 학습한다. world-model variant 는 Stage 1 `BEST_CHECKPOINT.json.epoch` 을 읽어 **로컬** `merged/{MODEL}_stage1_{MODE}/epoch-{Ewin}/` 을 base 로 사용 (YAML 의 `model_name_or_path` 를 런타임에 sed 로 치환).
- `stage2_merge.sh` 는 variant × 전 epoch 를 각각 merge + HF Hub push 한다 (`SaFD-00/...stage2-{base|{MODE}-world-model}-epoch{E}`).
- `stage2_eval.sh` 는 (a) baseline zero-shot 과 (b) variant × `--epochs` 리스트 기반 HF Hub repo sweep 을 수행 (로컬 adapter/merged 디렉토리 미참조). `step_accuracy` 기준 winner 를 각 adapter 디렉토리의 `BEST_CHECKPOINT[.json]` 에 기록 (Step Accuracy 정의: `ARCHITECTURE.md` §6).

## 산출물

모든 결과물은 `GUI-Model/outputs/` 단일 루트 아래에 **데이터셋 중심** 으로 모인다. `adapters/`·`merged/` 는 flat 네이밍 `{MODEL}_{detail}/`, `eval/` 만 중첩 구조를 유지한다. Stage 1 full/lora 산출물은 경로 접미사로 분리되어 공존 가능하다.

```
GUI-Model/outputs/{MB|AC}/
├── adapters/
│   ├── {model_short_name}_stage1_full/                          # Stage 1 full FT 체크포인트 + BEST_CHECKPOINT[.json]
│   ├── {model_short_name}_stage1_lora/                          # Stage 1 LoRA 체크포인트 + BEST_CHECKPOINT[.json]
│   ├── {model_short_name}_stage2_lora_base/                     # Stage 2 LoRA (base) 체크포인트 + BEST_CHECKPOINT[.json]
│   ├── {model_short_name}_stage2_lora_world_model_from_full/    # Stage 2 LoRA (상류=S1 full)
│   └── {model_short_name}_stage2_lora_world_model_from_lora/    # Stage 2 LoRA (상류=S1 lora)
├── eval/{model_short_name}/                                      # 중첩 유지 (epoch-{E} 서브폴더)
│   ├── stage1_eval/{base, {full|lora}_world_model/epoch-{E}}/    # generated_predictions, hungarian_metrics
│   └── stage2_eval/{base, lora_base/epoch-{E}, lora_world_model_from_{full|lora}/epoch-{E}}/
└── merged/
    ├── {model_short_name}_stage1_full/epoch-{E}/                # Stage 1 full-FT 병합 (epoch 별)
    ├── {model_short_name}_stage1_lora/epoch-{E}/                # Stage 1 LoRA 병합 (epoch 별)
    ├── {model_short_name}_stage2_lora_base/epoch-{E}/           # Stage 2 LoRA (base) 병합 (epoch 별)
    ├── {model_short_name}_stage2_lora_world_model_from_full/epoch-{E}/
    └── {model_short_name}_stage2_lora_world_model_from_lora/epoch-{E}/
```

HF Hub 레포지토리 네이밍 (epoch 별 개별 repo):

- `SaFD-00/{short}-{slug}stage1-full-world-model-epoch{E}`
- `SaFD-00/{short}-{slug}stage1-lora-world-model-epoch{E}`
- `SaFD-00/{short}-{slug}stage2-base-epoch{E}`                   (Stage 1 무관)
- `SaFD-00/{short}-{slug}stage2-full-world-model-epoch{E}`       (상류=S1 full)
- `SaFD-00/{short}-{slug}stage2-lora-world-model-epoch{E}`       (상류=S1 lora)

repo id 조립은 `scripts/_common.sh::hf_repo_id_stage{1,2}` 에 단일화되어 있다. `{E}` 는 각 checkpoint 의 `trainer_state.json` 에서 `int(round(epoch))` 로 추출한다.

## 모델 추가 방법

새 모델 추가 시 아래를 동기화해야 한다:

1. `gui-model.ipynb` Cell 3 의 `_MODEL_CONFIG` 딕셔너리에 모델 항목 추가 (`backend` 필드 포함)
2. `scripts/_common.sh` 의 `MODEL_ID`, `MODEL_TEMPLATE`, `ALL_MODELS` 에 동일 항목 추가
3. backend 가 기본값(`llamafactory`)이 아니면 `_common.sh` 의 `MODEL_BACKEND` 매핑에 등록
4. `backend=unsloth` 일 경우 `unsloth/configs/GUI-Model-{MB,AC}/stage{1,2}_*/...` YAML 은 notebook 의 "Unsloth Stage {1,2} YAML 일괄 생성" 셀에서 자동 생성된다 (Gemma-4 e2b/e4b 적용). 동일 모델이 LF `examples/custom/GUI-Model-{MB,AC}/stage{1,2}_*/` 아래에는 생성되지 않는다 (LF 생성 셀들이 `backend == "unsloth"` 을 스킵함).

## 코드 읽기 시작점

- [`gui-model.ipynb`](./gui-model.ipynb): 전체 파이프라인 기준
- [`scripts/_common.sh`](./scripts/_common.sh): path, dataset, model, logging 규약
- [`scripts/split_data.py`](./scripts/split_data.py): split 규칙
- [`scripts/_hungarian_eval.py`](./scripts/_hungarian_eval.py): Stage 1 metric
- [`scripts/_action_eval.py`](./scripts/_action_eval.py): Stage 2 metric

구조 설명은 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 작업 규칙은 [`AGENTS.md`](./AGENTS.md) 를 본다.
