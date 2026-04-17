# GUI-Model Architecture

`GUI-Model` 은 모바일 GUI World Modeling 이 Action Prediction 성능에 주는 영향을 검증하는 2-stage fine-tuning 파이프라인이다. 12개 Vision-Language 모델(Qwen, Gemma, LLaVA 계열)을 지원하며, notebook 이 오케스트레이션을 담당하고, `scripts/` 가 반복 실행용 자동화를 담당한다. 학습과 export 는 **모델별 백엔드(LlamaFactory 또는 Unsloth)** 가 수행하며, `scripts/_common.sh` 의 `MODEL_BACKEND` 매핑을 기준으로 내부 자동 분기된다.

## 0. Backend Selection

```
scripts/_common.sh::MODEL_BACKEND[model_short] → llamafactory | unsloth
  │
  ├── llamafactory (기본) → llamafactory-cli train/export
  │     ├── YAML: LlamaFactory/examples/train_custom/GUI-Model-{DS}/stage{1,2}_*
  │     └── 대상: Qwen2-VL, Qwen2.5-VL, Qwen3-VL, LLaVA 계열
  │
  └── unsloth             → scripts/_unsloth_train.py / _unsloth_merge.py
        ├── YAML: unsloth/configs/GUI-Model-{DS}/stage{1,2}_*
        └── 대상: google/gemma-4-E2B-it, google/gemma-4-E4B-it
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

1. Section 0: 환경 설정, 모델/데이터셋 config 정의, Stage 1/2 training YAML 및 Stage 1 eval YAML 생성
2. Section 1-2: `LlamaFactory/data/dataset_info.json` 등록
3. Section 3: Stage 1 full fine-tuning (모델+데이터셋별 개별 셀)
4. Section 4: Stage 1 평가 및 Hungarian F1 winner 선택
5. Section 5: Stage 1 merge 및 export (모델+데이터셋별 개별 셀)
6. Section 6: Stage 2 LoRA fine-tuning (모델+데이터셋별 개별 셀)
7. Section 7: Stage 2 평가 및 Overall Score winner 선택
8. Section 8: Stage 2 merge 및 export (모델+데이터셋별 개별 셀)

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
| gemma-4-e2b | google/gemma-4-E2B-it | gemma4 | **unsloth** |
| gemma-4-e4b | google/gemma-4-E4B-it | gemma4 | **unsloth** |
| llava-v1.6-mistral-7b | llava-hf/llava-v1.6-mistral-7b-hf | llava_next | llamafactory |
| llava-v1.6-vicuna-7b | llava-hf/llava-v1.6-vicuna-7b-hf | llava_next | llamafactory |
| llama3-llava-next-8b | llava-hf/llama3-llava-next-8b-hf | llava_next | llamafactory |

### 하이퍼파라미터

모든 모델은 동일한 데이터셋별 하이퍼파라미터를 사용한다. 하이퍼파라미터는 `_DATASET_CONFIG` 에서 관리된다.

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

- [`scripts/stage1_train.sh`](./scripts/stage1_train.sh)
  - `backend=llamafactory`: `examples/train_custom/GUI-Model-{DS}/stage1_full/{MODEL}.yaml`, `FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=${NPROC_PER_NODE}`
  - `backend=unsloth`: `unsloth/configs/GUI-Model-{DS}/stage1_full/{MODEL}.yaml`, `accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE} scripts/_unsloth_train.py`
  - `NPROC_PER_NODE` 는 `.env` 에서 관리 (기본값 2)
- [`scripts/stage1_eval.sh`](./scripts/stage1_eval.sh)
  - baseline zero-shot + checkpoint sweep (backend 독립)
  - `LlamaFactory/scripts/vllm_infer.py` 로 생성 (`cd "$LF_ROOT"` 내부에서 호출, `--dataset_dir '$LF_ROOT/data'` 절대 경로 필수)
  - `_hungarian_eval.py` 로 score/select
- [`scripts/stage1_merge.sh`](./scripts/stage1_merge.sh)
  - `BEST_CHECKPOINT` 를 읽고 backend 분기
  - `backend=llamafactory`: 임시 merge YAML 렌더 → `llamafactory-cli export`
  - `backend=unsloth`: `scripts/_unsloth_merge.py --mode full` (full FT 체크포인트 copy+push)
  - 산출물: `outputs/{DS}/merged/{MODEL}_stage1_full/` + HF Hub push

### Stage 2 automation

- [`scripts/stage2_train.sh`](./scripts/stage2_train.sh)
  - `{MODEL}/base.yaml`, `{MODEL}/world_model.yaml` 반복 실행
  - `backend=llamafactory`: llamafactory-cli (torchrun prefix 없음, 노트북 원본과 일치)
  - `backend=unsloth`: `accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE} scripts/_unsloth_train.py` (`NPROC_PER_NODE` 는 `.env` 관리, 기본값 2)
- [`scripts/stage2_eval.sh`](./scripts/stage2_eval.sh)
  - baseline zero-shot + `lora_base` / `lora_world_model` checkpoint sweep (backend 독립)
  - `LlamaFactory/scripts/vllm_infer.py` 호출 시 `cd "$LF_ROOT"` 내부에서 실행하고 `--dataset_dir '$LF_ROOT/data'` 절대 경로 필수
  - `lora_world_model` 평가는 로컬 `outputs/{DS}/merged/{MODEL}_stage1_full/` 를 base model 로 사용한다
- [`scripts/stage2_merge.sh`](./scripts/stage2_merge.sh)
  - 각 LoRA variant 의 `BEST_CHECKPOINT` 를 읽어 merge
  - `backend=llamafactory`: `llamafactory-cli export` → `merged_16bit`
  - `backend=unsloth`: `scripts/_unsloth_merge.py --mode lora` → `save_pretrained_merged(method="merged_16bit")`
  - 산출물: `outputs/{DS}/merged/{MODEL}_stage2_lora_{base,world_model}/` + HF Hub push

### Shell script CLI

```bash
bash scripts/stage1_train.sh --model qwen3-vl-8b --dataset MB
bash scripts/stage1_train.sh --model qwen3-vl-8b          # 전체 데이터셋
bash scripts/stage1_train.sh --dataset MB                  # 전체 모델
bash scripts/stage1_train.sh                               # 전체 모델 + 전체 데이터셋
```

## 5. 실행 데이터 흐름

```
raw JSONL + screenshots
  -> split_data.py
  -> dataset_info.json registration
  -> [per model] Stage 1 train
  -> [per model] Stage 1 eval
  -> BEST_CHECKPOINT
  -> [per model] Stage 1 merge
  -> outputs/{DS}/merged/{MODEL}_stage1_full
  -> [per model] Stage 2 train
  -> [per model] Stage 2 eval
  -> BEST_CHECKPOINT
  -> [per model] Stage 2 merge
  -> outputs/{DS}/merged/{MODEL}_stage2_lora_{base,world_model}
```

### 산출물 위치

모든 산출물은 `GUI-Model/outputs/` 단일 루트 아래에 **데이터셋 중심 + category 분리** 구조로 모인다.

```
GUI-Model/outputs/{DS}/
├── adapters/
│   ├── {model_short_name}_stage1_full/
│   │   ├── checkpoint-*/
│   │   ├── BEST_CHECKPOINT
│   │   └── BEST_CHECKPOINT.json
│   ├── {model_short_name}_stage2_lora_base/
│   │   ├── checkpoint-*/
│   │   └── BEST_CHECKPOINT
│   └── {model_short_name}_stage2_lora_world_model/
│       ├── checkpoint-*/
│       └── BEST_CHECKPOINT
├── eval/{model_short_name}/              # sub-hierarchy 가 있어 중첩 유지
│   ├── stage1_eval/
│   │   ├── base/
│   │   └── full_world_model/
│   │       └── checkpoint-*/
│   └── stage2_eval/
│       ├── base/
│       ├── lora_base/{base,checkpoint-*}/
│       └── lora_world_model/{base,checkpoint-*}/
└── merged/
    ├── {model_short_name}_stage1_full/
    ├── {model_short_name}_stage2_lora_base/
    └── {model_short_name}_stage2_lora_world_model/
```

### HuggingFace 업로드 ID 패턴

| Stage | 패턴 |
|-------|------|
| Stage 1 | `SaFD-00/{short_name}-{slug}stage1-world-model` |
| Stage 2 base | `SaFD-00/{short_name}-{slug}stage2-base` |
| Stage 2 world | `SaFD-00/{short_name}-{slug}stage2-world-model` |

`{slug}` 는 `mb-` (MobiBench) 또는 `ac-` (AndroidControl).

## 6. 메트릭과 winner selection

### Stage 1

- baseline: 각 모델의 zero-shot
- winner metric: `avg_hungarian_f1`
- winner 기록 위치:
  - `outputs/{DS}/adapters/{MODEL}_stage1_full/BEST_CHECKPOINT`
  - `outputs/{DS}/adapters/{MODEL}_stage1_full/BEST_CHECKPOINT.json`

### Stage 2

- baseline: 각 모델의 zero-shot
- 비교 대상:
  - `lora_base`
  - `lora_world_model`
- winner metric: `overall_score`
- winner 기록 위치:
  - `outputs/{DS}/adapters/{MODEL}_stage2_lora_base/BEST_CHECKPOINT`
  - `outputs/{DS}/adapters/{MODEL}_stage2_lora_world_model/BEST_CHECKPOINT`

## 7. 중요한 운영 제약

- `gui_model/` 패키지에는 핵심 파이프라인이 없다. 변경 작업은 notebook, shell script, custom YAML 경로를 우선 검토해야 한다.
- merge 스크립트는 `BEST_CHECKPOINT` 가 없으면 hard-fail 한다. fallback 동작은 없다.
- Stage 2 eval 과 merge 는 Stage 1 로컬 merge 결과물에 의존한다.
- merge 스크립트는 `.env` 또는 환경변수의 `HF_TOKEN` (HF Hub push 용) 과 Python `pyyaml` 을 전제로 한다.
- shell automation 은 bash 4+ 환경을 요구한다.
- 모델 추가 시 notebook `_MODEL_CONFIG` 와 `_common.sh` `MODEL_ID`/`MODEL_TEMPLATE`/`ALL_MODELS` 를 동시에 동기화해야 한다. backend 가 기본값(`llamafactory`)이 아니면 `MODEL_BACKEND` 도 동기화한다.
- `backend=unsloth` 모델 (Gemma-4 계열) 은 `unsloth/configs/GUI-Model-{DS}/stage{1,2}_*/...` YAML 을 사용하며, notebook 의 "Unsloth Stage {1,2} YAML 일괄 생성" 셀에서 자동 생성된다. LF `train_custom/`/`merge_custom/` YAML 생성 셀은 `backend == "unsloth"` 모델을 스킵하므로 Gemma 용 LF YAML 은 생성되지 않는다. Unsloth YAML 은 `cwd=UNSLOTH_ROOT` 기준 `../data/`, `../outputs/` 상대경로를 쓴다.
- Unsloth 체크포인트는 HF 표준 safetensors, LoRA adapter 는 PEFT 표준이므로 `vllm_infer.py` 가 backend 독립적으로 로드한다.
- `scripts/_unsloth_train.py` 는 모듈 최상단에서 `import unsloth` 를 선행시키고 `UNSLOTH_RETURN_LOGITS=1` 을 설정한다. trl ≥ 0.24 의 `SFTTrainer.compute_loss` 가 `entropy_from_logits(outputs.logits)` 를 직접 호출하기 때문이며, Unsloth 기본값(EMPTY_LOGITS) 과 충돌하면 `TypeError: 'function' object is not subscriptable` 로 첫 step 에서 실패한다.
- trl 0.24 / transformers 5.x API 매핑: `SFTConfig(max_length=...)`, `SFTTrainer(processing_class=...)` 를 사용한다. 구버전 키(`max_seq_length`, `tokenizer=`, `overwrite_output_dir`) 는 `TypeError` 를 낸다.
