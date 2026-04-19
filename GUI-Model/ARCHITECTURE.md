# GUI-Model Architecture

`GUI-Model` 은 모바일 GUI World Modeling 이 Action Prediction 성능에 주는 영향을 검증하는 2-stage fine-tuning 파이프라인이다. 12개 Vision-Language 모델(Qwen, Gemma, LLaVA 계열)을 지원하며, notebook 이 오케스트레이션을 담당하고, `scripts/` 가 반복 실행용 자동화를 담당한다. 학습과 export 는 **모델별 백엔드(LlamaFactory 또는 Unsloth)** 가 수행하며, `scripts/_common.sh` 의 `MODEL_BACKEND` 매핑을 기준으로 내부 자동 분기된다.

## 0. Backend Selection

```
scripts/_common.sh::MODEL_BACKEND[model_short] → llamafactory | unsloth
  │
  ├── llamafactory (기본) → llamafactory-cli train/export
  │     ├── YAML: LlamaFactory/examples/custom/GUI-Model-{DS}/stage{1,2}_{full,eval,lora,merge}
  │     └── 대상: Qwen2-VL, Qwen2.5-VL, Qwen3-VL, LLaVA 계열
  │
  └── unsloth             → scripts/_unsloth_train.py / _unsloth_merge.py
        ├── YAML: unsloth/configs/GUI-Model-{DS}/stage{1,2}_*
        └── 대상: unsloth/gemma-4-E2B-it, unsloth/gemma-4-E4B-it
```

평가 파이프라인 (`scripts/stage{1,2}_eval.sh`) 은 backend 독립이다.
`vllm_infer.py` 가 Unsloth 가 저장한 표준 HF 체크포인트/PEFT adapter 를 동일하게 로드한다.

## 1. 실행 구조

### 핵심 엔트리포인트

- [`gui-model.ipynb`](./gui-model.ipynb)
  - 환경 설치
  - 모델 config (`_MODEL_CONFIG`, `backend` 필드 포함) + 데이터셋 config (`_DATASET_CONFIG`) 정의
  - LlamaFactory YAML 자동 생성
  - `dataset_info.json` 등록 (LlamaFactory 전용; Unsloth 는 JSONL 직접 로드)
  - Stage 1/2 학습, 평가, merge 순차 실행 (모델+데이터셋별 개별 셀)
- [`scripts/`](./scripts)
  - `stage{1,2}_{train,eval,merge}.sh`: `--model MODEL --dataset DS` 플래그 방식 CLI, backend 분기 내장
  - `_unsloth_train.py`, `_unsloth_merge.py`: Unsloth 전용 Python entrypoint
  - notebook 으로 한 번 생성한 LlamaFactory YAML 과 dataset 등록 결과를 재사용하는 반복 실행 경로
- [`LlamaFactory/`](./LlamaFactory) (backend=llamafactory)
  - `llamafactory-cli train` / `llamafactory-cli export`
  - [`LlamaFactory/scripts/vllm_infer.py`](./LlamaFactory/scripts/vllm_infer.py) (모든 backend 공통 추론 도구; eval 스크립트가 `cd "$LF_ROOT" && python scripts/vllm_infer.py …` 로 호출)
- [`unsloth/`](./unsloth) (backend=unsloth)
  - `from unsloth import FastModel, FastVisionModel`
  - `scripts/_unsloth_train.py` 가 내부적으로 호출

### 실제 코드 기준 섹션 순서

`gui-model.ipynb` 는 아래 순서를 기준으로 작성되어 있다.

1. Section 0: 환경 설정, 모델/데이터셋 config 정의, Stage 1 YAML (full · lora 양쪽) / Stage 2 YAML (base · world-model-full · world-model-lora) / Stage 1 eval YAML 생성
2. Section 1-2: `LlamaFactory/data/dataset_info.json` 등록
3. Section 3: Stage 1 fine-tuning (노트북 셀은 default=full 기준 실행. LoRA 는 `scripts/stage1_train.sh --stage1-mode lora`)
4. Section 4: Stage 1 평가 및 Hungarian F1 winner 선택
5. Section 5: Stage 1 merge 및 export (full/lora 모드별 분리 산출)
6. Section 6: Stage 2 LoRA fine-tuning (world-model variant 는 상류 Stage 1 모드에 따라 `world-model-full.yaml` 또는 `world-model-lora.yaml` 선택)
7. Section 7: Stage 2 평가 및 Overall Score winner 선택
8. Section 8: Stage 2 merge 및 export

## 2. 모델 설정

### 모델 레지스트리

`gui-model.ipynb` Cell 3 의 `_MODEL_CONFIG`, `scripts/_common.sh` 의 `MODEL_ID`/`MODEL_TEMPLATE`/`MODEL_BACKEND` 가 모두 동기화되어야 한다.

| short_name | model_id | template | backend |
|------------|----------|----------|---------|
| qwen2-vl-2b | Qwen/Qwen2-VL-2B-Instruct | qwen2_vl | llamafactory |
| qwen2-vl-7b | Qwen/Qwen2-VL-7B-Instruct | qwen2_vl | llamafactory |
| qwen2.5-vl-3b | Qwen/Qwen2.5-VL-3B-Instruct | qwen2_vl | llamafactory |
| qwen2.5-vl-7b | Qwen/Qwen2.5-VL-7B-Instruct | qwen2_vl | llamafactory |
| qwen3-vl-2b | Qwen/Qwen3-VL-2B-Instruct | qwen3_vl_nothink | llamafactory |
| qwen3-vl-4b | Qwen/Qwen3-VL-4B-Instruct | qwen3_vl_nothink | llamafactory |
| qwen3-vl-8b | Qwen/Qwen3-VL-8B-Instruct | qwen3_vl_nothink | llamafactory |
| gemma-4-e2b | unsloth/gemma-4-E2B-it | gemma4 | **unsloth** |
| gemma-4-e4b | unsloth/gemma-4-E4B-it | gemma4 | **unsloth** |
| llava-v1.6-mistral-7b | llava-hf/llava-v1.6-mistral-7b-hf | llava_next | llamafactory |
| llava-v1.6-vicuna-7b | llava-hf/llava-v1.6-vicuna-7b-hf | llava_next | llamafactory |
| llama3-llava-next-8b | llava-hf/llama3-llava-next-8b-hf | llava_next | llamafactory |

### 하이퍼파라미터

모든 모델은 동일한 데이터셋별 하이퍼파라미터를 사용한다. 하이퍼파라미터는 `_DATASET_CONFIG` 에서 관리된다.

`gradient_accumulation_steps` 는 `_DATASET_CONFIG` 상수가 아니라 notebook Cell 6 에서 런타임 계산된다. 불변식:

```
global_batch = per_device_train_batch_size * gradient_accumulation_steps * NPROC_PER_NODE
            == GLOBAL_BATCH_SIZE  (기본 64)

gradient_accumulation_steps = GLOBAL_BATCH_SIZE / (per_device * NPROC_PER_NODE)
```

`per_device_train_batch_size` 는 프레임워크별 메모리 기준 상수 (LlamaFactory=2, Unsloth=1). `NPROC_PER_NODE` 는 `.env` 에서 읽는 GPU 수 (기본 2). Cell 6 이 `_derive_grad_accum()` 으로 역계산해 CONFIGS 의 `stage1.gradient_accumulation_steps` / `stage2.gradient_accumulation_steps` 에 주입하며, 나누어떨어지지 않으면 `ValueError` 로 중단한다 (silent rounding 금지).

## 3. 데이터와 설정 계약

### 데이터 디렉토리

```
data/
├── MobiBench/
│   ├── gui-model_stage1.jsonl
│   ├── gui-model_stage1_train.jsonl
│   ├── gui-model_stage1_test.jsonl
│   ├── gui-model_stage2.jsonl
│   ├── gui-model_stage2_train.jsonl
│   ├── gui-model_stage2_test.jsonl
│   └── images/
└── AndroidControl/
    └── ...
```

- Stage 1 은 random split 이다.
- Stage 2 는 action type 기준 stratified split 이다.
- [`scripts/split_data.py`](./scripts/split_data.py) 는 `AndroidControl` Stage 2 에 대해 기본적으로 `30000`개를 먼저 stratified subsample 한 뒤 split 한다.

### 데이터셋 이름 규약

| 용도 | MobiBench | AndroidControl |
|------|-----------|----------------|
| `data/` 아래 실제 디렉토리 | `MobiBench` | `AndroidControl` |
| shell script 단축 코드 | `MB` | `AC` |
| LLaMA-Factory dataset prefix | `GUI-Model-MB` | `GUI-Model-AC` |
| `outputs/` 하위 최상위 디렉토리 | `MB` | `AC` |

### LLaMA-Factory 등록

- notebook Section 1-2 가 `LlamaFactory/data/dataset_info.json` 를 갱신한다.
- JSONL 파일 경로는 `../../data/{DATASET_NAME}/...` 형태의 상대 경로로 등록된다.
- JSONL 내부 `images` 값은 `{DATASET_NAME}/images/...` 형태의 상대 경로를 유지한다.
- `vllm_infer.py` 호출 시 `--dataset_dir`에 **절대 경로** (`$LF_ROOT/data`)를 전달해야 한다. 상대 경로(`"data"`)를 사용하면 HF datasets 캐시가 다른 cwd 에서 생성된 미해석 이미지 경로를 재사용하여 `FileNotFoundError`가 발생할 수 있다.

## 4. 파이프라인 컴포넌트

### 로컬 오케스트레이션 레이어

- [`gui-model.ipynb`](./gui-model.ipynb): 전체 실험 실행의 기준 경로
- [`scripts/_common.sh`](./scripts/_common.sh): 공통 path, `.env`, dataset 매핑, 모델 레지스트리, bash 4+ 가드, logging
- [`scripts/split_data.py`](./scripts/split_data.py): split 및 AndroidControl Stage 2 subsample
- [`scripts/_hungarian_eval.py`](./scripts/_hungarian_eval.py): Stage 1 metric 집계 및 winner 선택
- [`scripts/_action_eval.py`](./scripts/_action_eval.py): Stage 2 metric 집계 및 winner 선택

### Stage 1 automation

Stage 1 은 `--stage1-mode {full|lora}` 로 finetuning 방식을 선택한다 (기본: `full`). 모드별로 YAML 경로 · adapter 경로 · merged 경로 · HF Hub ID 가 모두 접미사로 분리되어 공존한다.

- [`scripts/stage1_train.sh`](./scripts/stage1_train.sh)
  - `backend=llamafactory`: `examples/custom/GUI-Model-{DS}/stage1_${MODE}/{MODEL}_world-model.yaml`, `FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=${NPROC_PER_NODE}`
  - `backend=unsloth`: `unsloth/configs/GUI-Model-{DS}/stage1_${MODE}/{MODEL}_world-model.yaml`, `accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE} scripts/_unsloth_train.py`
  - LF full YAML 은 `finetuning_type: full`, LF lora YAML 은 `finetuning_type: lora` + `lora_rank/alpha/target/dropout` 블록을 포함한다.
  - `NPROC_PER_NODE` 는 `.env` 에서 관리 (기본값 2). notebook Cell 6 이 이 값을 읽어 각 YAML 의 `gradient_accumulation_steps` 를 역계산하므로, GPU 수 변경 시 `.env` 수정 후 YAML 생성 셀(9/11)을 재실행해야 한다.
- [`scripts/stage1_eval.sh`](./scripts/stage1_eval.sh)
  - baseline zero-shot + HF merged world-model (`...-stage1-${MODE}-world-model`) 평가 (backend 독립)
  - 결과 경로: `outputs/{DS}/eval/{MODEL}/stage1_eval/{base, ${MODE}_world_model}/`
  - `_hungarian_eval.py` 로 score/select → adapter 경로의 `BEST_CHECKPOINT` 기록
- [`scripts/stage1_merge.sh`](./scripts/stage1_merge.sh)
  - `outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/BEST_CHECKPOINT` 를 읽고 backend 분기
  - `backend=llamafactory`:
    - full 모드: 임시 merge YAML 의 `model_name_or_path` 를 winner checkpoint 로 설정 → `llamafactory-cli export`
    - lora 모드: `model_name_or_path: {base_model}` + `adapter_name_or_path: {winner}` + `finetuning_type: lora` 블록 삽입
  - `backend=unsloth`: `scripts/_unsloth_merge.py --mode {full|lora}`
  - 산출물: `outputs/{DS}/merged/{MODEL}_stage1_${MODE}/` + HF Hub push (`...-stage1-${MODE}-world-model`)

### Stage 2 automation

Stage 2 스크립트도 `--stage1-mode {full|lora}` 를 받아 world-model variant 가 참조할 상류 Stage 1 소스를 결정한다 (기본: `full`). `base` variant 는 Stage 1 모드와 무관하게 항상 실행된다.

- [`scripts/stage2_train.sh`](./scripts/stage2_train.sh)
  - 반복 실행: `{MODEL}_base.yaml` + `{MODEL}_world-model-${MODE}.yaml` (LF: `examples/custom/GUI-Model-{DS}/stage2_lora/`, Unsloth: `unsloth/configs/GUI-Model-{DS}/stage2_lora/`)
  - `backend=llamafactory`: llamafactory-cli (torchrun prefix 없음, 노트북 원본과 일치)
  - `backend=unsloth`: `accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE} scripts/_unsloth_train.py` (`NPROC_PER_NODE` 는 `.env` 관리, 기본값 2)
- [`scripts/stage2_eval.sh`](./scripts/stage2_eval.sh)
  - baseline zero-shot + `lora_base` / `lora_world_model_${MODE}` checkpoint sweep (backend 독립)
  - `LlamaFactory/scripts/vllm_infer.py` 호출 시 `cd "$LF_ROOT"` 내부에서 실행하고 `--dataset_dir '$LF_ROOT/data'` 절대 경로 필수
  - `lora_world_model_${MODE}` 평가는 HF `...-stage2-${MODE}-world-model` 을 로드한다 (stage2_merge 후)
- [`scripts/stage2_merge.sh`](./scripts/stage2_merge.sh)
  - 각 LoRA variant 의 `BEST_CHECKPOINT` 를 읽어 merge
  - `backend=llamafactory`: `llamafactory-cli export` → `merged_16bit`
  - `backend=unsloth`: `scripts/_unsloth_merge.py --mode lora` → `save_pretrained_merged(method="merged_16bit")`
  - 산출물: `outputs/{DS}/merged/{MODEL}_stage2_lora_{base, world_model_from_${MODE}}/` + HF Hub push (`...-stage2-base`, `...-stage2-${MODE}-world-model`)

### Shell script CLI

```bash
bash scripts/stage1_train.sh --model qwen3-vl-8b --dataset MB                        # full (default)
bash scripts/stage1_train.sh --model qwen3-vl-8b                                     # 전체 데이터셋 full
bash scripts/stage1_train.sh --model gemma-4-e2b --dataset MB --stage1-mode lora     # LoRA 모드
bash scripts/stage2_train.sh --stage1-mode lora                                      # 전체 모델, world-model 은 S1-lora 기준
bash scripts/stage1_train.sh                                                         # 전체 모델 + 전체 DS + full
```

## 5. 실행 데이터 흐름

```
raw JSONL + screenshots
  -> split_data.py
  -> dataset_info.json registration
  -> [per model] Stage 1 train  (mode = full | lora)
  -> [per model] Stage 1 eval
  -> BEST_CHECKPOINT  (in adapters/..._stage1_{mode})
  -> [per model] Stage 1 merge
  -> outputs/{DS}/merged/{MODEL}_stage1_{mode}  +  HF Hub ...-stage1-{mode}-world-model
  -> [per model] Stage 2 train  (base + world-model-{mode})
  -> [per model] Stage 2 eval
  -> BEST_CHECKPOINT  (in adapters/..._stage2_lora_{base, world_model_from_{mode}})
  -> [per model] Stage 2 merge
  -> outputs/{DS}/merged/{MODEL}_stage2_lora_{base, world_model_from_{mode}}
```

### 산출물 위치

모든 산출물은 `GUI-Model/outputs/` 단일 루트 아래에 **데이터셋 중심 + category 분리** 구조로 모인다. Stage 1 full/lora 산출물은 경로 접미사로 분리되어 공존 가능하다.

```
GUI-Model/outputs/{DS}/
├── adapters/
│   ├── {model_short_name}_stage1_full/
│   ├── {model_short_name}_stage1_lora/
│   │   ├── checkpoint-*/
│   │   ├── BEST_CHECKPOINT
│   │   └── BEST_CHECKPOINT.json
│   ├── {model_short_name}_stage2_lora_base/
│   ├── {model_short_name}_stage2_lora_world_model_from_full/
│   └── {model_short_name}_stage2_lora_world_model_from_lora/
├── eval/{model_short_name}/              # sub-hierarchy 가 있어 중첩 유지
│   ├── stage1_eval/
│   │   ├── base/
│   │   ├── full_world_model/
│   │   └── lora_world_model/
│   └── stage2_eval/
│       ├── base/
│       ├── lora_base/
│       ├── lora_world_model_full/
│       └── lora_world_model_lora/
└── merged/
    ├── {model_short_name}_stage1_full/
    ├── {model_short_name}_stage1_lora/
    ├── {model_short_name}_stage2_lora_base/
    ├── {model_short_name}_stage2_lora_world_model_from_full/
    └── {model_short_name}_stage2_lora_world_model_from_lora/
```

### HuggingFace 업로드 ID 패턴

| Stage / variant | 패턴 |
|-------|------|
| Stage 1 (full FT) | `SaFD-00/{short_name}-{slug}stage1-full-world-model` |
| Stage 1 (LoRA)    | `SaFD-00/{short_name}-{slug}stage1-lora-world-model` |
| Stage 2 base      | `SaFD-00/{short_name}-{slug}stage2-base` |
| Stage 2 world (from S1 full) | `SaFD-00/{short_name}-{slug}stage2-full-world-model` |
| Stage 2 world (from S1 lora) | `SaFD-00/{short_name}-{slug}stage2-lora-world-model` |

`{slug}` 는 `mb-` (MobiBench) 또는 `ac-` (AndroidControl).

## 6. 메트릭과 winner selection

### Stage 1

- baseline: 각 모델의 zero-shot
- winner metric: `avg_hungarian_f1`
- winner 기록 위치 (MODE = `full` | `lora`):
  - `outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/BEST_CHECKPOINT`
  - `outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/BEST_CHECKPOINT.json`

### Stage 2

- baseline: 각 모델의 zero-shot
- 비교 대상 (스크립트의 `--stage1-mode` 값에 따라 world-model variant 상류 선택):
  - `lora_base`
  - `lora_world_model_${MODE}`  (`MODE` = `full` | `lora`)
- winner metric: `step_accuracy`
- winner 기록 위치:
  - `outputs/{DS}/adapters/{MODEL}_stage2_lora_base/BEST_CHECKPOINT`
  - `outputs/{DS}/adapters/{MODEL}_stage2_lora_world_model_from_${MODE}/BEST_CHECKPOINT`

#### Step Accuracy (SA) 정의

AndroidControl 데이터셋은 GT 에 `bounds` 필드가 영구 부재하고 element-index 기반
grounding 을 사용한다. IoU 기반 채점은 구조적으로 0 이 되므로, Stage 2 평가는 다음
정의를 따른다.

```
SA = (1/N) · Σ correct_i

correct_i = 1 iff (parse_ok ∧ type==gt.type ∧ field_match(type))
         = 0 otherwise
```

| GT type | field_match 조건 |
|---|---|
| `navigate_back` | (검증 필드 없음) → 항상 통과 |
| `finish` | (status 단일값 `"complete"`) → 항상 통과 |
| `click`, `long_click` | `str(pred.index) == str(gt.index)` |
| `scroll` | `norm(direction)` 일치 |
| `open_app` | `norm(params.app)` 일치 (top-level 평탄화 fallback 허용) |
| `input` | `norm(params.text)` 일치 (gt.index=null 무시) |

`norm(s) = str(s or '').strip().lower()` — 모든 string field 통일.

`action_metrics.json` 키:
- 1차: `step_accuracy` (winner 선택 기본 metric)
- 보조: `macro_step_accuracy` (7 type 평균), `parse_rate`, `type_accuracy`,
  `cond_index_acc` / `cond_dir_acc` / `cond_app_acc` / `cond_text_acc`,
  `per_type[t] = {count, type_acc, step_acc}`

Reference baselines (해석용):
- `type` random baseline: 1/7 ≈ 14.3%
- `scroll` majority baseline (`down`): 79.0%
- `finish.status` constant baseline: 100% (해석 무의미)

정본은 `gui-model.ipynb` Cell 139 이며 `scripts/_action_eval.py` 와 글자 단위 동치를 유지한다.
회귀 테스트는 `tests/test_action_eval.py` (30 케이스).

## 7. 중요한 운영 제약

- `gui_model/` 패키지에는 핵심 파이프라인이 없다. 변경 작업은 notebook, shell script, custom YAML 경로를 우선 검토해야 한다.
- merge 스크립트는 `BEST_CHECKPOINT` 가 없으면 hard-fail 한다. fallback 동작은 없다.
- Stage 2 eval 과 merge 는 Stage 1 로컬 merge 결과물에 의존한다. `--stage1-mode` 값에 따라 `outputs/{DS}/merged/{MODEL}_stage1_{full|lora}/` 중 하나가 전제가 된다.
- merge 스크립트는 `.env` 또는 환경변수의 `HF_TOKEN` (HF Hub push 용) 과 Python `pyyaml` 을 전제로 한다.
- shell automation 은 bash 4+ 환경을 요구한다.
- 모델 추가 시 notebook `_MODEL_CONFIG` 와 `_common.sh` `MODEL_ID`/`MODEL_TEMPLATE`/`ALL_MODELS` 를 동시에 동기화해야 한다. backend 가 기본값(`llamafactory`)이 아니면 `MODEL_BACKEND` 도 동기화한다.
- `backend=unsloth` 모델 (Gemma-4 계열) 은 `unsloth/configs/GUI-Model-{DS}/stage{1,2}_*/...` YAML 을 사용하며, notebook 의 "Unsloth Stage {1,2} YAML 일괄 생성" 셀에서 자동 생성된다. LF `examples/custom/` YAML 생성 셀은 `backend == "unsloth"` 모델을 스킵하므로 Gemma 용 LF YAML 은 생성되지 않는다. Unsloth YAML 은 `cwd=UNSLOTH_ROOT` 기준 `../data/`, `../outputs/` 상대경로를 쓴다.
- Unsloth 체크포인트는 HF 표준 safetensors, LoRA adapter 는 PEFT 표준이므로 `vllm_infer.py` 가 backend 독립적으로 로드한다.
- `scripts/_unsloth_train.py` 는 모듈 최상단에서 `import unsloth` 를 선행시키고 `UNSLOTH_RETURN_LOGITS=1` 을 설정한다. trl ≥ 0.24 의 `SFTTrainer.compute_loss` 가 `entropy_from_logits(outputs.logits)` 를 직접 호출하기 때문이며, Unsloth 기본값(EMPTY_LOGITS) 과 충돌하면 `TypeError: 'function' object is not subscriptable` 로 첫 step 에서 실패한다.
- **transformers 버전**: 최상위 `pyproject.toml [project].dependencies` 와 `setup.py INSTALL_REQUIRES` 에서 `transformers==5.5.4` 로 고정한다. 서브프로젝트(`unsloth/` `<=5.5.0`, `LlamaFactory/` `<=5.2.0`) transitive 상한과 충돌 시 README "환경 설치 > 전제" 의 회피 명령(`pip install --upgrade transformers==5.5.4` 또는 서브프로젝트 `--no-deps` 재설치) 을 사용한다. 서브프로젝트 `pyproject.toml` 은 수정하지 않는다.
- trl 0.24 / transformers 5.x API 매핑: `SFTConfig(max_length=...)`, `SFTTrainer(processing_class=...)` 를 사용한다. 구버전 키(`max_seq_length`, `tokenizer=`, `overwrite_output_dir`) 는 `TypeError` 를 낸다.
- **Unsloth ↔ deepspeed 비호환**: Unsloth 백엔드(Gemma-4 e2b/e4b) 는 deepspeed 가 env 에 설치된 상태로는 학습이 실패한다 (FastModel 의 메모리 최적화 / gradient checkpointing 이 deepspeed ZeRO 와 충돌, `accelerate launch` 가 deepspeed plugin 자동 활성화). 노트북 `gui-model.ipynb` Cell 5 (`%%bash` + `pip uninstall -y deepspeed`) 가 학습 직전 deepspeed 를 env 에서 제거한다. LlamaFactory 백엔드로 복귀 시 `pip install -e .` 로 `deepspeed>=0.10.0,<=0.18.4` 가 재설치된다.
- `scripts/_unsloth_train.py` 의 Unsloth Full FT 권장 사양 매핑 (Gemma-4 e2b/e4b stage1):
  - 모델 로드 시 `FastModel.from_pretrained(load_in_16bit=True, use_gradient_checkpointing="unsloth", full_finetuning=True)` 로 호출 → YAML `load_in_16bit`, `gradient_checkpointing` 키가 그대로 전달된다.
  - `gradient_checkpointing` 은 모델 로드 단계에서만 적용하며 `SFTConfig` 에는 전달하지 않는다 (이중 적용 방지).
  - 모델 로드 직후 `get_chat_template(tokenizer, cfg["template"])` 호출 → YAML `template: gemma4` 가 실제 chat token 정합성에 반영된다.
  - Full FT 분기에서 `freeze_vision_tower: true` 면 `vision_tower|vision_model|visual|image_encoder` 키워드를 포함한 named parameter 의 `requires_grad=False` 처리 후 frozen 텐서 수/파라미터 수를 stderr 로 출력한다.
  - `SFTConfig.optim` 은 YAML `optim` (기본 `adamw_torch_fused`) 을 사용한다. Gemma-4 e2b/e4b 는 stage1·stage2 override 에서 `adamw_torch_fused` 를 명시하며, multi-GPU DDP 환경에서는 `ddp_find_unused_parameters: true` 를 함께 사용해 frozen vision tower 로 인한 unused-grad 경고를 피한다.
