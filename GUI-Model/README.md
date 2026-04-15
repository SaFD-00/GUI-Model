# GUI-Model

모바일 GUI World Modeling 이 Action Prediction 성능에 미치는 영향을 검증하는 2-stage fine-tuning 파이프라인이다. 현재 코드 기준으로는 notebook 이 전체 실행의 기준이고, `scripts/` 가 반복 실행용 자동화 레이어이며, 실제 학습/평가 엔진은 저장소 내부 `LlamaFactory/` 가 담당한다.

## 개요

- Stage 1: `screenshot + UI XML + action -> next UI XML`
- Stage 2: `screenshot + UI XML + task -> action JSON`
- 비교 실험:
  - `Exp-1`: Stage 1 world model 품질 평가
  - `stage2`: base model 에서 바로 Stage 2 LoRA
  - `stage1+stage2`: Stage 1 merged model 위에서 Stage 2 LoRA

실제 파이프라인 로직은 [`gui-model.ipynb`](./gui-model.ipynb), [`scripts/`](./scripts), [`LlamaFactory/`](./LlamaFactory) 조합으로 구성된다. [`gui_model/`](./gui_model) 패키지는 배포용 스텁만 포함한다.

## 디렉토리 구조

```
GUI-Model/
├── gui-model.ipynb
├── scripts/
├── data/
├── LlamaFactory/
├── gui_model/
├── setup.py
├── README.md
├── ARCHITECTURE.md
└── AGENTS.md
```

## 환경 설치

```bash
pip install -e .
if [ ! -d LlamaFactory ]; then
  git clone https://github.com/hiyouga/LlamaFactory.git
fi
pip install -e "./LlamaFactory[torch,metrics]"
pip install -r LlamaFactory/requirements/metrics.txt -r LlamaFactory/requirements/deepspeed.txt
pip install vllm
```

추가 전제:

- Python 3.10+
- bash 4+ (`scripts/_common.sh` 기준)
- merge/export 단계에서는 `HF_TOKEN`, `rsync`, `pyyaml`

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

생성 파일:

- `gui-model_stage1_train.jsonl`
- `gui-model_stage1_test.jsonl`
- `gui-model_stage2_train.jsonl`
- `gui-model_stage2_test.jsonl`

## 실행 방법

### 1. notebook 경로

[`gui-model.ipynb`](./gui-model.ipynb) 의 섹션 순서대로 실행한다.

1. Section 0: 환경 설정, dataset config, YAML 생성
2. Section 1-2: `dataset_info.json` 등록
3. Section 3: Stage 1 학습
4. Section 4: Stage 1 평가 및 winner 선택
5. Section 5: Stage 1 merge/export
6. Section 6: Stage 2 학습
7. Section 7: Stage 2 평가 및 winner 선택
8. Section 8: Stage 2 merge/export

### 2. shell script 경로

shell script 는 notebook 으로 한 번 생성된 YAML 과 `LlamaFactory/data/dataset_info.json` 이 이미 있다는 전제에서 동작한다.

```bash
bash scripts/stage1_train.sh MB
bash scripts/stage1_eval.sh MB
bash scripts/stage1_merge.sh MB

bash scripts/stage2_train.sh MB
bash scripts/stage2_eval.sh MB
bash scripts/stage2_merge.sh MB
```

지원 인자:

- `MB`: MobiBench
- `AC`: AndroidControl
- `all`: 둘 다 순차 실행

주요 동작:

- `stage1_eval.sh` 는 baseline + checkpoint sweep 뒤 `avg_hungarian_f1` 기준 winner 를 `BEST_CHECKPOINT` 로 기록한다.
- `stage1_merge.sh` 는 해당 winner 를 읽어 `outputs/{DS}/stage1_merged/` 를 만든다.
- `stage2_eval.sh` 는 baseline + `lora_base` / `lora_world_model` checkpoint sweep 뒤 `overall_score` 기준 winner 를 기록한다.
- `stage2_merge.sh` 는 해당 winner 를 읽어 `outputs/{DS}/stage2_merged/{base,world_model}/` 를 만든다.

## 산출물

주요 결과물은 모두 `LlamaFactory/` 아래에 쌓인다.

- `LlamaFactory/saves/{MB|AC}/...`
  - checkpoints
  - eval 결과
  - `BEST_CHECKPOINT`
- `LlamaFactory/outputs/{MB|AC}/stage1_merged/`
- `LlamaFactory/outputs/{MB|AC}/stage2_merged/{base,world_model}/`

## 코드 읽기 시작점

- [`gui-model.ipynb`](./gui-model.ipynb): 전체 파이프라인 기준
- [`scripts/_common.sh`](./scripts/_common.sh): path, dataset, logging 규약
- [`scripts/split_data.py`](./scripts/split_data.py): split 규칙
- [`scripts/_hungarian_eval.py`](./scripts/_hungarian_eval.py): Stage 1 metric
- [`scripts/_action_eval.py`](./scripts/_action_eval.py): Stage 2 metric

구조 설명은 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 작업 규칙은 [`AGENTS.md`](./AGENTS.md) 를 본다.
