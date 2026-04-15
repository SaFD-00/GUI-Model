# GUI-Model Architecture

`GUI-Model` 은 모바일 GUI World Modeling 이 Action Prediction 성능에 주는 영향을 검증하는 2-stage fine-tuning 파이프라인이다. 현재 코드 기준으로는 notebook 이 오케스트레이션을 담당하고, `scripts/` 가 반복 실행용 자동화를 담당하며, 실제 학습과 export 는 저장소 내부 `LlamaFactory/` 가 수행한다.

## 1. 실행 구조

### 핵심 엔트리포인트

- [`gui-model.ipynb`](./gui-model.ipynb)
  - 환경 설치
  - YAML 생성
  - `dataset_info.json` 등록
  - Stage 1/2 학습, 평가, merge 순차 실행
- [`scripts/`](./scripts)
  - notebook 으로 한 번 생성한 YAML 과 dataset 등록 결과를 재사용하는 반복 실행 경로
- [`LlamaFactory/`](./LlamaFactory)
  - `llamafactory-cli train`
  - `llamafactory-cli export`
  - `scripts/vllm_infer.py`

### 실제 코드 기준 섹션 순서

`gui-model.ipynb` 는 아래 순서를 기준으로 작성되어 있다.

1. Section 0: 환경 설정, 데이터셋 config 정의, Stage 1/2 training YAML 및 Stage 1 eval YAML 생성
2. Section 1-2: `LlamaFactory/data/dataset_info.json` 등록
3. Section 3: Stage 1 full fine-tuning
4. Section 4: Stage 1 평가 및 Hungarian F1 winner 선택
5. Section 5: Stage 1 merge 및 export
6. Section 6: Stage 2 LoRA fine-tuning
7. Section 7: Stage 2 평가 및 Overall Score winner 선택
8. Section 8: Stage 2 merge 및 export

## 2. 데이터와 설정 계약

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
| `outputs/`, `saves/` 하위 디렉토리 | `MB` | `AC` |

### LLaMA-Factory 등록

- notebook Section 1-2 가 `LlamaFactory/data/dataset_info.json` 를 갱신한다.
- JSONL 파일 경로는 `../../data/{DATASET_NAME}/...` 형태의 상대 경로로 등록된다.
- JSONL 내부 `images` 값은 각 JSONL 파일 기준 `images/...` 상대 경로를 유지한다.

## 3. 파이프라인 컴포넌트

### 로컬 오케스트레이션 레이어

- [`gui-model.ipynb`](./gui-model.ipynb): 전체 실험 실행의 기준 경로
- [`scripts/_common.sh`](./scripts/_common.sh): 공통 path, `.env`, dataset 매핑, bash 4+ 가드, logging
- [`scripts/split_data.py`](./scripts/split_data.py): split 및 AndroidControl Stage 2 subsample
- [`scripts/_hungarian_eval.py`](./scripts/_hungarian_eval.py): Stage 1 metric 집계 및 winner 선택
- [`scripts/_action_eval.py`](./scripts/_action_eval.py): Stage 2 metric 집계 및 winner 선택

### Stage 1 automation

- [`scripts/stage1_train.sh`](./scripts/stage1_train.sh)
  - `examples/custom/GUI-Model-{DS}/stage1_full/qwen3_vl_8b_gui.yaml`
  - `FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=4`
- [`scripts/stage1_eval.sh`](./scripts/stage1_eval.sh)
  - baseline zero-shot + checkpoint sweep
  - `scripts/vllm_infer.py` 로 생성
  - `_hungarian_eval.py` 로 score/select
- [`scripts/stage1_merge.sh`](./scripts/stage1_merge.sh)
  - `BEST_CHECKPOINT` 를 읽어 임시 merge YAML 렌더
  - `llamafactory-cli export`
  - `exports/...` 를 `outputs/{DS}/stage1_merged/` 로 rsync

### Stage 2 automation

- [`scripts/stage2_train.sh`](./scripts/stage2_train.sh)
  - `base.yaml`, `world_model.yaml` 반복 실행
  - notebook 원본과 맞추기 위해 torchrun prefix 를 붙이지 않는다
- [`scripts/stage2_eval.sh`](./scripts/stage2_eval.sh)
  - baseline zero-shot + `lora_base` / `lora_world_model` checkpoint sweep
  - `lora_world_model` 평가는 로컬 `outputs/{DS}/stage1_merged/` 를 base model 로 사용한다
- [`scripts/stage2_merge.sh`](./scripts/stage2_merge.sh)
  - 각 LoRA variant 의 `BEST_CHECKPOINT` 를 읽어 merge
  - `outputs/{DS}/stage2_merged/{base,world_model}/` 로 로컬 복사

## 4. 실행 데이터 흐름

```
raw JSONL + screenshots
  -> split_data.py
  -> dataset_info.json registration
  -> Stage 1 train
  -> Stage 1 eval
  -> BEST_CHECKPOINT
  -> Stage 1 merge
  -> outputs/{DS}/stage1_merged
  -> Stage 2 train
  -> Stage 2 eval
  -> BEST_CHECKPOINT
  -> Stage 2 merge
  -> outputs/{DS}/stage2_merged/{base,world_model}
```

### 산출물 위치

- `LlamaFactory/saves/{DS}/stage1_full/full_world_model/`
  - training checkpoints
  - `BEST_CHECKPOINT`
  - `BEST_CHECKPOINT.json`
- `LlamaFactory/saves/{DS}/stage1_eval/...`
  - Stage 1 baseline 및 checkpoint sweep 결과
- `LlamaFactory/saves/{DS}/stage2_lora/{lora_base,lora_world_model}/`
  - LoRA checkpoints
  - 각 variant 의 `BEST_CHECKPOINT`
- `LlamaFactory/saves/{DS}/stage2_eval/...`
  - Stage 2 baseline 및 checkpoint sweep 결과
- `LlamaFactory/outputs/{DS}/stage1_merged/`
- `LlamaFactory/outputs/{DS}/stage2_merged/{base,world_model}/`

## 5. 메트릭과 winner selection

### Stage 1

- baseline: `Qwen/Qwen3-VL-8B-Instruct`
- winner metric: `avg_hungarian_f1`
- winner 기록 위치:
  - `saves/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT`
  - `saves/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT.json`

### Stage 2

- baseline: `Qwen/Qwen3-VL-8B-Instruct`
- 비교 대상:
  - `lora_base`
  - `lora_world_model`
- winner metric: `overall_score`
- winner 기록 위치:
  - `saves/{DS}/stage2_lora/lora_base/BEST_CHECKPOINT`
  - `saves/{DS}/stage2_lora/lora_world_model/BEST_CHECKPOINT`

## 6. 중요한 운영 제약

- `gui_model/` 패키지에는 핵심 파이프라인이 없다. 변경 작업은 notebook, shell script, custom YAML 경로를 우선 검토해야 한다.
- merge 스크립트는 `BEST_CHECKPOINT` 가 없으면 hard-fail 한다. fallback 동작은 없다.
- Stage 2 eval 과 merge 는 Stage 1 로컬 merge 결과물에 의존한다.
- merge 스크립트는 `.env` 또는 환경변수의 `HF_TOKEN`, `rsync`, `pyyaml` 을 전제로 한다.
- shell automation 은 bash 4+ 환경을 요구한다.
