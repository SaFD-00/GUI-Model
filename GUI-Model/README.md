# GUI-Model

모바일 GUI World Modeling 이 Action Prediction 성능에 미치는 영향을 검증하는 2-stage fine-tuning 파이프라인이다. 현재 코드 기준으로는 notebook 이 전체 실행의 기준이고, `scripts/` 가 반복 실행용 자동화 레이어이며, 실제 학습/평가 엔진은 단일 conda env (`gui-model`) + 노트북 (`gui-model.ipynb`) + 저장소 내부 [`LlamaFactory/`](./LlamaFactory) 가 담당한다.

## 개요

- Stage 1: `screenshot + UI XML + action -> next UI XML`
- Stage 2: `screenshot + UI XML + task -> action JSON`
- 비교 실험:
  - `Exp-1`: Stage 1 world model 품질 평가
  - `stage2`: base model 에서 바로 Stage 2 LoRA
  - `stage1+stage2`: Stage 1 merged model 위에서 Stage 2 LoRA

실제 파이프라인 로직은 [`gui-model.ipynb`](./gui-model.ipynb), [`scripts/`](./scripts), [`LlamaFactory/`](./LlamaFactory) 조합으로 구성된다. [`gui_model/`](./gui_model) 패키지는 배포용 스텁만 포함한다.

## 지원 모델

| # | model_id | short_name | template |
|---|----------|------------|----------|
| 1 | Qwen/Qwen2-VL-2B-Instruct | qwen2-vl-2b | qwen2_vl |
| 2 | Qwen/Qwen2-VL-7B-Instruct | qwen2-vl-7b | qwen2_vl |
| 3 | Qwen/Qwen2.5-VL-3B-Instruct | qwen2.5-vl-3b | qwen2_vl |
| 4 | Qwen/Qwen2.5-VL-7B-Instruct | qwen2.5-vl-7b | qwen2_vl |
| 5 | Qwen/Qwen3-VL-4B-Instruct | qwen3-vl-4b | qwen3_vl_nothink |
| 6 | Qwen/Qwen3-VL-8B-Instruct | qwen3-vl-8b | qwen3_vl_nothink |
| 7 | Qwen/Qwen3.5-4B-Base | qwen3.5-4b-base | qwen3_5_nothink |
| 8 | Qwen/Qwen3.5-9B-Base | qwen3.5-9b-base | qwen3_5_nothink |

> Qwen3.5-Base 는 LlamaFactory 가 multimodal `hf_model_type=qwen3_5` 로 인식하며 (Qwen3-VL 과 동일 그룹), `template=qwen3_5_nothink` 로 학습한다. 추론 시 `vllm_infer.py` 에 `--enable_thinking False` 가 자동 주입된다.

### 모델 계열별 image budget

학습 YAML 의 `image_max_pixels` / `image_min_pixels` 는 vision encoder patch-size (factor) 와 학습 데이터셋에 따라 결정된다 (`gui-model.ipynb` Cell 5 의 `MODEL_FAMILY_CONFIG` + `_DATASET_CONFIG[ds]["image_overrides"]`). token 예산은 **학습 데이터셋** 으로 정해지고, 학습된 모델이 어떤 ds 를 평가하든 학습 시 budget 을 그대로 사용한다 (학습-추론 mismatch 방지).

| family | patch | merge | factor | min_tokens | min_pixels |
|--------|-------|-------|--------|-----------|------------|
| Qwen2-VL · Qwen2.5-VL | 14 | 2 | 28 | 4 | 3,136 (= 4 × 28²) |
| Qwen3-VL · Qwen3.5 | 16 | 2 | 32 | 4 | 4,096 (= 4 × 32²) |

| 학습 DS | max_tokens | Qwen2/2.5-VL `max_pixels` | Qwen3-VL/3.5 `max_pixels` |
|---|---|---|---|
| AndroidControl (AC), MonkeyCollection (MC) | 2,048 (family default) | 1,605,632 (= 2048 × 28²) | 2,097,152 (= 2048 × 32²) |
| AndroidControl_2 (AC2) | 5,400 (dataset override) | 4,233,600 (= 5400 × 28²) | 5,529,600 (= 5400 × 32²) |

> 평가측 (`scripts/_common.sh::build_infer_cmd`) 은 `TRAIN_DATASET` 환경변수로 학습 DS 를 식별하여 동일 budget 을 적용한다. 즉 AC2 로 학습한 모델은 AC1·MC·MB 평가에도 5400-tokens 예산을 사용한다.

## 디렉토리 구조

```
GUI-Model/
├── gui-model.ipynb               # env: gui-model
├── scripts/
│   ├── _common.sh                # 공통 path / 모델 레지스트리
│   ├── stage{1,2}_{train,eval,merge}.sh
│   ├── _hungarian_eval.py        # Stage 1 metric
│   ├── _action_eval.py           # Stage 2 metric
│   ├── eval_viewer.py            # Stage 1/2 모델·epoch HTML 비교 뷰어
│   └── split_data.py
├── data/
├── LlamaFactory/                 # 학습/추론 엔진 (clone)
│   ├── examples/custom/          # LlamaFactory YAML (노트북이 생성)
│   └── scripts/vllm_infer.py     # 추론 도구
├── gui_model/                    # 배포용 스텁
├── pyproject.toml                # core deps + extras [llamafactory]
├── setup.py                      # local subproject file:// direct ref
├── .env.example                  # HF_TOKEN, NPROC_PER_NODE
├── README.md
├── ARCHITECTURE.md
└── AGENTS.md
```

## 환경 설치

단일 conda env (`gui-model`) 에 `pyproject.toml` 의 공통 deps + `setup.py::EXTRAS["llamafactory"]` (`transformers>=4.56.0,<5` 등) 를 설치한다. 서브프로젝트 (`./LlamaFactory`) 의 transitive 상한 (`transformers<=5.2.0`) 과 우리의 `transformers>=4.56.0,<5` 는 4.56–4.57.x 구간에서 겹치므로 한 번에 풀린다. `--no-deps` 회피 단계는 더 이상 필요 없다.

```bash
conda create -n gui-model python=3.12 -y
conda activate gui-model
cd /path/to/GUI-Model
PIP_USER=0 pip install --no-user -e ./LlamaFactory
PIP_USER=0 pip install --no-user -e '.[llamafactory]'
```

deepspeed 가 기본 포함되며, `llamafactory-cli train` / `llamafactory-cli export` 가 엔진이다.

### `.env` 변수

`.env.example` 를 복사해 `.env` 를 만든다. 노트북 Section 0 (Cell 5) 와 `scripts/_common.sh` 가 이 파일을 source 한다.

| 변수 | 기본 | 허용값 | 설명 |
|------|------|--------|------|
| `HF_TOKEN` | — | (string) | HF Hub push (merge 단계). |
| `NPROC_PER_NODE` | `2` | `1`, `2`, `4`, `8` | node 당 GPU 수 (single node, torchrun world size). |
| `GPU_TYPE` | `H100` | `RTX5090`, `A100`, `H100` | GPU 종류. 노트북이 (모델 size × GPU) 표로 `per_device_train_batch_size` 를 결정한다. |

#### `per_device_train_batch_size` (size × GPU)

| 모델 size | RTX5090 (32GB) | A100 (80GB) | H100 (80GB) |
|-----------|----------------|-------------|-------------|
| 2B        | 4              | 8           | 8           |
| 3-4B      | 2              | 4           | 4           |
| 7-9B      | 1              | 2           | 2           |

`GLOBAL_BATCH_SIZE = 64` 를 유지하기 위해 `gradient_accumulation_steps = 64 / (per_device × NPROC_PER_NODE)` 가 자동 역계산된다 (정수가 아니면 `ValueError`). 표 값을 바꾸면 4 가지 GPU 수 (1/2/4/8) 모두에서 정수가 되는지 확인한다.

> `.env` 의 `GPU_TYPE` / `NPROC_PER_NODE` 를 수정한 뒤에는 노트북 Section 0 의 CONFIGS 셀과 Stage 1/2 YAML 생성 셀을 다시 실행해야 새 값이 YAML 에 반영된다.

### PIP_USER 플래그 / 전제

- `PIP_USER=0` / `--no-user` 는 root 유저 + `PYTHONUSERBASE` 조합에서 pip 가 deps 를 user-site (예: `/root/.local/workspace/python-packages`) 로 흘려 conda env `bin/` 에 `accelerate` 같은 CLI entry point 가 만들어지지 않는 사고를 막는다.
- Python 3.10 이상, 3.13 미만
- bash 4+ (`scripts/_common.sh` 기준)
- `transformers>=4.56.0,<5` 로 고정 (vllm 0.11.2 의 `transformers<5` 제약 + LlamaFactory 서브프로젝트 `<=5.2.0` 와의 4.56–4.57.x 교집합). 변경 시 `setup.py::EXTRAS["llamafactory"]` 와 `pyproject.toml` 양쪽을 함께 갱신한다.
- merge/export 단계에서는 `HF_TOKEN` (HF Hub push 용) 를 `.env` 에 둔다.

### PATH / user-site 정책

`scripts/_common.sh` 는 conda env 가 활성화돼 있을 때 `$CONDA_PREFIX/bin` 을 PATH 최상단에 고정한다. user-site (`PYTHONUSERBASE/.../bin`) 에 낡은 `accelerate` CLI 가 남아 있어도 env 소속 CLI 가 먼저 잡히도록 보장하기 위함이며, user-site 자체는 유지한다.

conda env 미활성 상태에서 `scripts/*.sh` 를 실행하면 스크립트가 바로 중단된다. 먼저 activate:

```bash
conda activate gui-model
```

### 깨진 env 복구

user-site 에 손상된 패키지가 섞여 `pip install -e '.[llamafactory]'` 가 "already satisfied" 로 넘어가거나 CLI shebang 이 base env python 을 가리키는 상태라면:

```bash
# 문제 패키지만 env 에 강제 재설치 (예: accelerate)
PIP_USER=0 pip install --no-user --force-reinstall --no-deps "accelerate>=1.3.0,<=1.11.0"

# 또는 전체 재해석
PIP_USER=0 pip install --no-user --force-reinstall --no-deps -e '.[llamafactory]'
```

검증:

```bash
which accelerate                 # → $CONDA_PREFIX/bin/accelerate
head -1 "$(which accelerate)"    # → #!$CONDA_PREFIX/bin/python...
python -c "import accelerate, torch; print(accelerate.__file__, torch.__version__)"
```

## 데이터 준비

학습 대상 DS 는 **AndroidControl (AC)**, **AndroidControl_2 (AC_2)**, **MonkeyCollection (MC)** 세 가지. **MobiBench (MB)** 는 평가 전용 벤치마크이므로 split 하지 않고 단일 파일 두 개만 둔다.

```
data/
├── AndroidControl/                 # 학습 + 평가 (Stage 1 + 2, ID/OOD split)
│   ├── gui-model_stage1.jsonl
│   ├── gui-model_stage2.jsonl
│   └── images/
├── AndroidControl_2/                # 학습 + 평가 (Stage 1 + 2, 단일 test)
│   ├── gui-model_stage1.jsonl
│   ├── gui-model_stage2.jsonl
│   ├── gui-model_stage{1,2}_{train,test}.jsonl   # 사전 분할 데이터
│   ├── gui-model_stage1_test_without_open_app.jsonl  # script 전용 변형
│   └── episodes_meta.jsonl
│   # NOTE: images/ 디렉토리 없음 — JSONL `images` 경로가 "AndroidControl/images/..." 로
│   #       AC 의 images 를 그대로 참조한다.
├── MonkeyCollection/                # Stage 1 학습 + 평가 (Stage 2 없음)
│   ├── gui-model_stage1.jsonl       # 약 100K
│   └── images/
└── MobiBench/                       # 평가 전용 벤치마크 (단일 파일)
    ├── gui-model_stage1.jsonl
    ├── gui-model_stage2.jsonl
    └── images/
```

AC 는 **앱 도메인 일반화** 평가를 위해 **Stage 1 과 Stage 2 모두 in-domain / out-of-domain** 으로 분리한다 (두 stage 가 동일한 app partition 을 공유 — Stage 2 OOD 앱이 Stage 1 train 에도 포함되지 않도록 보장). AC_2 는 AC 와 schema 가 동일하지만 **단일 test (ID/OOD 분리 없음)** 로 사전 분할되어 제공된다 — `split_data.py` 를 다시 돌릴 필요 없음. MC 는 Stage 2 데이터가 없고 메타도 없으므로 Stage 1 random split 만 수행한다. MB 는 split 하지 않는다.

```bash
# 1) AC: 에피소드 메타데이터 (primary_app = 전경 앱 package_name) 추출
python scripts/extract_androidcontrol_metadata.py \
    --output data/AndroidControl/episodes_meta.jsonl

# 2) AC: Stage 1 + Stage 2 모두 ID/OOD split (동일 partition 공유).
python scripts/split_data.py --dataset AndroidControl

# 3) AC_2: 사전 분할 상태로 제공 — split 명령 불필요.
#         (data/AndroidControl_2/gui-model_stage{1,2}_{train,test}.jsonl 이미 존재)

# 4) MC: Stage 1 random split 만 (Stage 2 자동 skip).
python scripts/split_data.py --dataset MonkeyCollection

# 5) MB: split 불필요. data/MobiBench/gui-model_stage{1,2}.jsonl 만 있으면 평가 가능.
```

분할 규칙:

- **App partition (AC 공유)**: 앱 집합을 셔플 → OOD 버킷이 `--stage2-test-ood-size` 를 채울 때까지 먼저 할당, 나머지는 ID 버킷. 같은 (id_apps, ood_apps) 쌍을 Stage 1 / Stage 2 양쪽에서 재사용한다.
- **Stage 1 (AC)**: 위 partition 으로 entries 를 라우팅 → ID 풀에서 `test_id_size` 행 random sample → 잔여 + (옵션) null-app 행으로 `train_size` 까지 random sample → OOD 풀에서 `test_ood_size` 행 random sample.
- **Stage 1 (MC)**: 메타 없음 → random split (`--stage1-ratio`, 기본 0.95).
- **Stage 2 (AC)**: ID 풀에서 `test_id_size` 행을 action-type stratified 로 샘플 → 나머지 + null-app 행을 합쳐 `train_size` 로 stratified subsample. OOD 풀에서 `test_ood_size` 행을 stratified 로 샘플.
- **AC_2 (Stage 1 + Stage 2)**: 사전 분할 단일 test. 평가는 `_hungarian_eval.py` / `_action_eval.py` 의 single-pair overall 모드로 채점.
- **MB**: split 없음. `gui-model_stage1.jsonl` 과 `gui-model_stage2.jsonl` 각각이 평가 세트 전체. `_hungarian_eval.py` / `_action_eval.py` 모두 single-pair overall 모드로 채점.

산출물 (학습 DS):

```
data/AndroidControl/
├── gui-model_stage1.jsonl
├── gui-model_stage1_train.jsonl       # 50K (default)
├── gui-model_stage1_test_id.jsonl     # 3K  (default, in-domain apps)
├── gui-model_stage1_test_ood.jsonl    # 3K  (default, out-of-domain apps)
├── episodes_meta.jsonl
└── gui-model_stage2_{train,test_id,test_ood}.jsonl

data/AndroidControl_2/
├── gui-model_stage1.jsonl
├── gui-model_stage1_train.jsonl       # ~67K
├── gui-model_stage1_test.jsonl        # ~3.5K  (단일 test, ID/OOD 없음)
├── gui-model_stage1_test_without_open_app.jsonl  # script 전용 변형 (notebook 미등록)
├── gui-model_stage2.jsonl
├── gui-model_stage2_train.jsonl       # ~28K
├── gui-model_stage2_test.jsonl        # ~1.5K
└── episodes_meta.jsonl

data/MonkeyCollection/
├── gui-model_stage1.jsonl
├── gui-model_stage1_train.jsonl       # 95%
└── gui-model_stage1_test.jsonl        # 5%
```

## 실행 방법

### 1. notebook 경로

`gui-model` env 에서 [`gui-model.ipynb`](./gui-model.ipynb) 를 섹션 순서대로 실행한다.

> **Global batch size 자동 계산**: Section 0 의 CONFIGS 셀이 `.env` 의 `GPU_TYPE` / `NPROC_PER_NODE` 와
> 모델 size 별 `_PER_DEVICE_BS_BY_SIZE[size][GPU_TYPE]` 표에서 `per_device_train_batch_size` 를 결정하고,
> `gradient_accumulation_steps` 를 역계산해 `GLOBAL_BATCH_SIZE=64` 를 유지한다. GPU 종류/수 변경 시
> `.env` 만 수정하고 Section 0 의 CONFIGS 셀과 YAML 생성 셀을 다시 실행하면 된다 (위 ".env 변수" 표 참고).
>
> **AC 하이퍼파라미터는 모델 크기 3 단(2B / 3-4B / 7-9B) 으로 공유**된다 (`_SIZE_CONFIG_AC`).
> 상세 tier 표는 [`ARCHITECTURE.md`](./ARCHITECTURE.md) §2 "하이퍼파라미터" 참조.

1. Section 0: 환경 설정, dataset config, 모델 config (`_MODEL_CONFIG`), YAML 생성 (Stage 1 full · lora 학습용 + Stage 2 base · world-model-full · world-model-lora 학습용 · merge 용). **Stage 1 eval YAML 은 더 이상 생성하지 않는다** (쉘 스크립트가 HF Hub merged 모델을 직접 sweep).
2. Section 1-2: `dataset_info.json` 등록
3. Section 3: Stage 1 학습 — 노트북 매트릭스: 8 모델 × 3 데이터셋 ({AC, AC_2, MC}) × 2 모드 ({full, lora}) (§3.1 Full FT, §3.2 LoRA)
4. Section 4: **Stage 1 merge (모든 epoch 을 각각 merge + HF Hub push)** — 동일 매트릭스 (§4.1 Full, §4.2 LoRA). 누락 checkpoint 슬롯은 SKIP+경고
5. Section 5: **Stage 1 평가 (HF Hub 에서 `--epochs` 지정 merged 모델 pull). winner 자동 선정 없음 — 사용자가 결과를 보고 Stage 2 에 사용할 epoch 을 수동 결정.**
6. Section 6: Stage 2 학습 (LoRA) — 8 모델 × 2 데이터셋 ({AC, AC_2}). MC 는 Stage 2 데이터/YAML 부재로 제외. world-model variant 는 `--stage1-epoch N` 으로 상류 Stage 1 epoch 의 local merged 를 base 로 사용
7. Section 7: **Stage 2 merge (variant × 모든 epoch 각각 merge + HF Hub push)** — 동일 매트릭스, 누락 슬롯은 SKIP+경고
8. Section 8: **Stage 2 평가 (HF Hub pull sweep, ID+OOD 동시. `action_metrics.json` 에 overall/in_domain/out_of_domain 3 섹션)**

### 2. shell script 경로

shell script 는 notebook 으로 한 번 생성된 **학습/merge YAML** 과 `LlamaFactory/data/dataset_info.json` 이 이미 있다는 전제에서 동작한다. **Stage 1 eval 은 YAML 을 사용하지 않고 쉘 스크립트가 직접 HF Hub merged 모델을 sweep 한다.**

> **MobiBench 평가 엔트리 자동 보장**: `scripts/_common.sh::ensure_eval_only_dataset_info()` 가 stage{1,2}_eval.sh 진입 시 `GUI-Model-MB_stage{1,2}` 단일 파일 엔트리를 `dataset_info.json` 에 idempotent 하게 추가한다.

Stage 1 은 `--stage1-mode full|lora` (기본 `full`), Stage 2 는 `--stage2-mode full|lora` (기본 `lora`) 로 finetuning 방식을 선택한다. world-model variant 학습·머지·평가는 `--stage1-epoch N` 으로 사용할 Stage 1 epoch 을 직접 지정한다 (자동 winner 선정은 없다).

```bash
# Stage 1 Full FT — train → merge → eval (AC 학습, AC/AC_2/MC/MB 교차 평가)
bash scripts/stage1_train.sh --model qwen3-vl-8b --dataset AC
bash scripts/stage1_merge.sh --model qwen3-vl-8b --dataset AC                                # 모든 epoch push
bash scripts/stage1_eval.sh  --model qwen3-vl-8b --train-dataset AC --eval-datasets AC,AC_2,MC,MB \
     --variants base,full_world_model --epochs 1,2,3                                         # HF Hub sweep

# Stage 1 LoRA — MC 학습
bash scripts/stage1_train.sh --model qwen3-vl-4b --dataset MC --stage1-mode lora
bash scripts/stage1_merge.sh --model qwen3-vl-4b --dataset MC --stage1-mode lora
bash scripts/stage1_eval.sh  --model qwen3-vl-4b --train-dataset MC --eval-datasets AC,MC,MB \
     --stage1-mode lora --variants base,lora_world_model --epochs 1,2,3

# Stage 1 — AC_2 학습 (token budget 5400 자동 적용)
bash scripts/stage1_train.sh --model qwen3-vl-8b --dataset AC_2 --stage1-mode lora
bash scripts/stage1_merge.sh --model qwen3-vl-8b --dataset AC_2 --stage1-mode lora
bash scripts/stage1_eval.sh  --model qwen3-vl-8b --train-dataset AC_2 --eval-datasets AC_2,MB \
     --stage1-mode lora --variants base,lora_world_model --epochs 1,2,3

# 전체 모델 × 전체 데이터셋 일괄 sweep (--model / --dataset 생략)
bash scripts/stage1_train.sh --stage1-mode full   # 8 모델 × {AC, AC_2, MC}
bash scripts/stage1_merge.sh --stage1-mode full   # 학습 안 된 슬롯은 [WARN] 출력 후 SKIP

# Stage 2 — AC, AC_2 지원 (MC 는 Stage 2 데이터/YAML 없음)
bash scripts/stage2_train.sh --model qwen3-vl-8b --dataset AC \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora
bash scripts/stage2_merge.sh --model qwen3-vl-8b --dataset AC \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora
bash scripts/stage2_eval.sh  --model qwen3-vl-8b --train-dataset AC --eval-datasets AC,MB \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora \
     --variants base,lora_base,lora_world_model --epochs 1,2,3

# Stage 2 — AC_2 (train-dataset AC_2 도 동일하게 지원)
bash scripts/stage2_train.sh --model qwen3-vl-8b --dataset AC_2 \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora
bash scripts/stage2_merge.sh --model qwen3-vl-8b --dataset AC_2 \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora

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
- `--dataset DS`: `AC` | `AC_2` | `MC` | `all` (기본: `all`) — Stage 1 학습 대상 DS. **MB 는 평가 전용이므로 거절**. **Stage 2 는 MC 미지원** (Stage 2 데이터/YAML 없음 → `--dataset MC` 시 stage2_train.sh 가 YAML 부재로 중단, stage2_eval.sh 는 `--train-dataset MC` 거절).
- `--stage1-mode MODE`: `full` | `lora` (기본: `full`)
- `--stage2-mode MODE`: `full` | `lora` (기본: `lora`) — stage2 스크립트 전용
- `--stage1-epoch N`: Stage 2 world-model variant 에서 참조할 상류 Stage 1 epoch (stage2 전용)

**평가 스크립트 (`stage{1,2}_eval.sh`)**: 학습 DS 와 평가 DS 를 분리.
- `--model MODEL`
- `--train-dataset DS`: stage1_eval = `AC` | `AC_2` | `MC`, stage2_eval = `AC` | `AC_2` (필수)
- `--eval-datasets LIST`: 콤마 구분. 허용값 `AC,AC_2,MC,MB`.
- `--stage1-mode`, `--stage2-mode`, `--stage1-epoch` (상동)
- `--epochs LIST`: 콤마 구분 정수 (기본 `1,2,3`).
- `--variants LIST`: 콤마 구분 평가 변형 목록.

주요 동작:

- `stage1_merge.sh` 는 모든 `checkpoint-*/` 를 돌면서 `trainer_state.json.epoch` 기반으로 `merged/{MODEL}_stage1_${MODE}/epoch-{E}/` 로 local merge + HF 에 `SaFD-00/{short}-{slug}world-model-stage1-{MODE}-epoch{E}` 푸시. **Skip 동작**: checkpoint 가 없는 (model, dataset) 슬롯은 `[WARN]` 출력 후 SKIP, 다음 슬롯으로 계속 진행 (`--model all` sweep 친화). 요약 라인에 `merged / skipped / failed` 카운트가 함께 출력된다.
- `stage1_eval.sh` 는 `--variants` + `--epochs` 로 지정된 HF repo 를 pull 해 `hungarian_metrics.json` 을 산출한다. 각 `(variant, EVAL_DS)` 마다 정규 산출 직후 추론 재실행 없이 `_hungarian_eval.py score --exclude-action open_app` 한 번을 더 호출하여 sibling `on-{EVAL_DS}-without-open_app/` 에 필터된 jsonl + 메트릭 + `predict_results.json` 을 idempotent 저장한다. 필터 test JSONL 은 `data/{DATADIR}/{prefix}_stage1{_test{_id,_ood}}_without_open_app.jsonl` 로 자동 생성/재사용.
- `stage2_train.sh` 는 `stage2_{full|lora}/{MODEL}_{base,world-model-{full,lora}}.yaml` 을 학습한다. world-model variant 는 `--stage1-epoch N` 으로 지정된 로컬 `merged/{MODEL}_stage1_${STAGE1_MODE}/epoch-${N}/` 를 base 로 사용 (YAML `model_name_or_path` 런타임 sed 치환).
- `stage2_merge.sh` 는 각 epoch 를 merge + HF push. **Skip 동작**은 stage1_merge 와 동일 (누락 슬롯 `[WARN]` 후 SKIP, 요약 라인에 `merged / skipped / failed`):
  - base variant: `SaFD-00/{short}-{slug}base-stage2-{MODE2}-epoch{E2}`
  - world-model: `SaFD-00/{short}-{slug}world-model-stage1-{MODE1}-epoch{E1}-stage2-{MODE2}-epoch{E2}`
- `stage2_eval.sh` 는 ID + OOD 테스트 파일을 **동시에** 추론하고 `_action_eval.py score` 가 `overall` / `in_domain` / `out_of_domain` 3 섹션을 한 `action_metrics.json` 에 기록한다.

### 평가 결과 시각 비교 (`scripts/eval_viewer.py`)

`stage{1,2}_eval.sh` 산출물을 행 정렬된 HTML 로 비교한다.

```bash
python scripts/eval_viewer.py
python scripts/eval_viewer.py --stages 1 --datasets on-AC
python scripts/eval_viewer.py --model qwen3-vl-8b
python scripts/eval_viewer.py --stages 1 --variants base full_world_model/epoch-3
python scripts/eval_viewer.py --data-dir AC_2 --model qwen3-vl-8b qwen2.5-vl-7b
```

`--data-dir {AC|AC_2}` 로 데이터/산출물 루트를 선택한다 (기본 `AC`). `AC_2` 는 `data/AndroidControl_2/` + `outputs/AC_2/` 를 사용 (Stage 1/2 모두 split 없는 단일 test 파일). `--model` 은 다중 모델을 받아 모델별로 반복 실행한다.

산출물 위치: `outputs/{AC|AC_2}/eval/{MODEL}/stage{1,2}_eval/{pairs_on-AC.html, pairs_on-MB.html, pairs_summary.md}`. Stage 1 은 정규/필터 4개 dataset (`on-AC`, `on-AC-without-open_app`, `on-MB`, `on-MB-without-open_app`) 에 대해 단일 호출로 4개 HTML 과 통합 `pairs_summary.md` 를 만든다.

## 산출물

모든 결과물은 `GUI-Model/outputs/` 단일 루트 아래에 **데이터셋 중심** 으로 모인다. `adapters/`·`merged/` 는 flat 네이밍, `eval/` 만 중첩 구조를 유지한다.

```
GUI-Model/outputs/{MB|AC}/
├── adapters/
│   ├── {model}_stage1_{full,lora}/                                       # Stage 1 체크포인트
│   ├── {model}_stage2_{full,lora}_base/                                  # Stage 2 base
│   └── {model}_stage2_{full,lora}_world_model_from_{full,lora}/          # Stage 2 world-model
├── eval/{model}/
│   ├── stage1_eval/{base, {full,lora}_world_model/epoch-{E}}/   # 각 variant 아래 on-{EVAL_DS}/ + on-{EVAL_DS}-without-open_app/ 쌍
│   └── stage2_eval/{base,
│                     {full,lora}_base/epoch-{E},
│                     {full,lora}_world_model_from_{full,lora}_ep{E1}/epoch-{E2}}/
└── merged/
    ├── {model}_stage1_{full,lora}/epoch-{E}/
    ├── {model}_stage2_{full,lora}_base/epoch-{E}/
    └── {model}_stage2_{full,lora}_world_model_from_{full,lora}/epoch-{E}/
```

HF Hub 레포지토리 네이밍:

- `SaFD-00/{short}-{slug}world-model-stage1-{full,lora}-epoch{E}`                       (Stage 1)
- `SaFD-00/{short}-{slug}base-stage2-{full,lora}-epoch{E2}`                             (Stage 2 base)
- `SaFD-00/{short}-{slug}world-model-stage1-{M1}-epoch{E1}-stage2-{M2}-epoch{E2}`       (Stage 2 world-model)

repo id 조립은 `scripts/_common.sh::hf_repo_id_stage1` / `hf_repo_id_stage2_base` / `hf_repo_id_stage2_world_model` 에 단일화되어 있다.

## 모델 추가 방법

새 모델 추가 시 아래를 동기화해야 한다:

1. `gui-model.ipynb` Cell 5 의 `_MODEL_CONFIG` 에 모델 항목 추가 (`MODEL_FAMILY_CONFIG` 에도 해당 family 의 image-pixel config 가 있는지 확인).
2. `scripts/_common.sh` 의 `MODEL_ID`, `MODEL_TEMPLATE`, `ALL_MODELS` 에 동일 항목 추가.
3. 노트북 Section 0 "Stage {1,2} YAML 일괄 생성" 셀이 재실행되면 YAML (`LlamaFactory/examples/custom/...`) 이 자동 생성된다. MC 는 Stage 1 전용이므로 `stage2_*/` YAML 은 MC 에 대해 생성되지 않는다 (`_STAGE1_ONLY` guard).

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
- [`scripts/eval_viewer.py`](./scripts/eval_viewer.py): Stage 1/2 평가 결과 HTML 비교 뷰어

구조 설명은 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 작업 규칙은 [`AGENTS.md`](./AGENTS.md) 를 본다.
