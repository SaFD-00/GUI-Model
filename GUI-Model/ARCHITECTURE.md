# GUI-Model Architecture

`GUI-Model` 은 모바일 GUI World Modeling 이 Action Prediction 성능에 주는 영향을 검증하는 2-stage fine-tuning 파이프라인이다. 12개 Vision-Language 모델(Qwen, Gemma, LLaVA 계열)을 지원하며, **백엔드별로 분리된 conda env + 노트북 한 쌍** 이 오케스트레이션을 담당하고, `scripts/` 가 반복 실행용 자동화를 담당한다. 학습과 export 는 env 안에 설치된 백엔드(LlamaFactory 또는 Unsloth)가 수행한다.

## 0. Backend Selection

backend 는 **env + notebook 1:1 대응** 으로 물리적으로 분리된다.

```
conda env                       notebook                              엔진
──────────────────────────────  ───────────────────────────────────   ──────────────────────────────
gui-model-llamafactory          gui-model-llamafactory.ipynb           llamafactory-cli train/export
  ├── pip install -e '.[llamafactory]'                                    YAML: LlamaFactory/examples/custom/
  ├── transformers==5.5.4  (고정 — Qwen/LLaVA 검증)                             GUI-Model-{DS}/stage{1,2}_{full,lora}
  ├── ./LlamaFactory (file://)                                           대상: Qwen2-VL, Qwen2.5-VL, Qwen3-VL, LLaVA
  ├── deepspeed
  └── vllm

gui-model-unsloth               gui-model-unsloth.ipynb                scripts/_unsloth_train.py
  ├── pip install -e '.[unsloth]'                                          / _unsloth_merge.py
  ├── transformers>=5.5.4  (상한 해제 — Gemma-4 loader)                 YAML: unsloth/configs/GUI-Model-{DS}/stage{1,2}_*
  ├── ./unsloth[huggingface,triton] (file://)                          대상: google/gemma-4-E2B-it, google/gemma-4-E4B-it
  ├── bitsandbytes                                                     (deepspeed 미설치 — FastModel / ZeRO 충돌 회피)
  └── vllm
```

shell 자동화 레이어의 `scripts/_common.sh::MODEL_BACKEND` 매핑과 `stage{1,2}_{train,merge}.sh` 의 backend 분기 로직은 그대로 유지된다. 사용자가 엉뚱한 env 에서 해당 백엔드의 CLI/모듈이 없는 모델을 `--model` 로 지정하면 shell 단계에서 즉시 `ImportError` / `command not found` 로 중단된다 (의도된 가드).

평가 파이프라인 (`scripts/stage{1,2}_eval.sh`) 은 backend 독립이다.
`vllm_infer.py` 가 Unsloth 가 저장한 표준 HF 체크포인트/PEFT adapter 를 동일하게 로드하므로, 두 env 중 어느 쪽에서든 HF Hub merged repo 를 pull 해 평가할 수 있다.

## 1. 실행 구조

### 핵심 엔트리포인트

- [`gui-model-llamafactory.ipynb`](./gui-model-llamafactory.ipynb) — env `gui-model-llamafactory`, 10개 모델 (Qwen2/2.5/3-VL, LLaVA 계열)
  - 환경 설치: `pip install -e '.[llamafactory]'`, `git clone LlamaFactory/` (이미 존재하면 skip)
  - 모델 config (`_MODEL_CONFIG`, 10 models) + 데이터셋 config (`_DATASET_CONFIG`) 정의
  - LlamaFactory Stage 1/2 YAML 자동 생성 (Unsloth 생성 셀은 없음)
  - `LlamaFactory/data/dataset_info.json` 등록
  - Stage 1/2 학습·평가·merge 순차 실행
- [`gui-model-unsloth.ipynb`](./gui-model-unsloth.ipynb) — env `gui-model-unsloth`, 2개 모델 (Gemma-4-E2B/E4B)
  - 환경 설치: `pip install -e '.[unsloth]'`, `git clone unsloth/` (이미 존재하면 skip)
  - 모델 config (`_MODEL_CONFIG`, 2 models) + 데이터셋 config (동일)
  - Unsloth Stage 1/2 YAML 자동 생성 (LlamaFactory 생성 셀은 없음)
  - `dataset_info.json` 등록은 없음 (Unsloth 는 JSONL 직접 로드)
  - Stage 1/2 학습·평가·merge 순차 실행
- [`scripts/`](./scripts)
  - `stage{1,2}_{train,eval,merge}.sh`: `--model MODEL --dataset DS` 플래그 방식 CLI, backend 분기 내장. 분리된 env 에서도 `_common.sh::MODEL_BACKEND` 를 통해 호출 경로가 결정된다 (env 가 모델을 "갖고 있느냐" 는 사용자 책임, shell 은 CLI 미존재 시 빨리 실패).
  - `_unsloth_train.py`, `_unsloth_merge.py`: Unsloth 전용 Python entrypoint — `gui-model-unsloth` env 에서만 동작.
  - 두 노트북에서 한 번 생성한 백엔드별 YAML 과 dataset 등록 결과를 재사용하는 반복 실행 경로.
- [`LlamaFactory/`](./LlamaFactory) — `gui-model-llamafactory` env 에서 활성
  - `llamafactory-cli train` / `llamafactory-cli export`
  - [`LlamaFactory/scripts/vllm_infer.py`](./LlamaFactory/scripts/vllm_infer.py) (모든 backend 공통 추론 도구; eval 스크립트가 `cd "$LF_ROOT" && python scripts/vllm_infer.py …` 로 호출)
- [`unsloth/`](./unsloth) — `gui-model-unsloth` env 에서 활성
  - `from unsloth import FastModel, FastVisionModel`
  - `scripts/_unsloth_train.py` 가 내부적으로 호출

### 실제 코드 기준 섹션 순서

두 노트북은 섹션 번호/구조를 동일하게 유지해 서로 1:1 대응된다. 아래 순서를 각자 독립적으로 실행한다.

1. Section 0: 환경 설정, 모델/데이터셋 config 정의, 해당 백엔드의 Stage 1 학습 YAML (full · lora 양쪽) / Stage 2 학습 YAML (full · lora 양쪽 × base · world-model-full · world-model-lora) 생성. 학습 DS 는 `_DATASET_CONFIG` 의 AC + MC 이며, Stage 2 YAML 은 `_STAGE1_ONLY` guard 로 MC 를 skip 한다. **Stage {1,2} merge YAML 은 더 이상 노트북에서 사전 생성하지 않는다** — `scripts/stage{1,2}_merge.sh` 가 runtime 에 임시 YAML 을 만든다.
2. Section 1-2: 데이터 준비. LlamaFactory 노트북은 `LlamaFactory/data/dataset_info.json` 을 갱신(AC/MC 는 `*_stage1_train/test`, AC 는 추가로 `*_stage2_train/test_id/test_ood`, MobiBench 는 `_EVAL_ONLY_BENCHMARKS` 루프가 단일 파일 entry 등록) — **Unsloth 노트북에는 이 등록 셀이 없다** (Unsloth 는 JSONL 직접 로드).
3. Section 3: Stage 1 fine-tuning (`--stage1-mode full|lora`)
4. Section 4: **Stage 1 merge (모든 epoch 각각 merge + HF Hub push)**
5. Section 5: **Stage 1 평가 — HF Hub epoch 별 merged 모델 sweep. Hungarian F1 metric 산출만 하고, 어떤 epoch 을 Stage 2 에 쓸지는 사용자가 결과를 보고 `--stage1-epoch` 로 지정 (자동 winner 선정 없음).**
6. Section 6: Stage 2 fine-tuning. `--stage2-mode {full|lora}` 로 full/lora 분기. world-model variant 는 `--stage1-epoch N` 으로 local `merged/{M}_stage1_${MODE}/epoch-${N}/` 를 base 로 사용.
7. Section 7: **Stage 2 merge (variant × 전 epoch 각각 merge + HF Hub push)**
8. Section 8: **Stage 2 평가 — ID + OOD 두 test 파일 (`gui-model_stage2_test_{id,ood}.jsonl`) 동시 sweep. `action_metrics.json` 에 `overall / in_domain / out_of_domain` 3 섹션으로 Step Accuracy 저장.**

## 2. 모델 설정

### 모델 레지스트리

`gui-model-llamafactory.ipynb` / `gui-model-unsloth.ipynb` 각 노트북의 Section 0 `_MODEL_CONFIG` (해당 백엔드 모델만 포함), `scripts/_common.sh` 의 `MODEL_ID`/`MODEL_TEMPLATE`/`MODEL_BACKEND` (12개 모델 전체) 가 모두 동기화되어야 한다.

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

하이퍼파라미터는 **3 단 구조**로 해석된다 (두 노트북 모두 Section 0 CONFIGS 셀에서 동일 로직):

1. `_DATASET_CONFIG[ds].stage{1,2}` — 데이터셋 공통 baseline (학습 대상 DS: AC, MC).
2. `_SIZE_CONFIG_AC[size].stage{1, 1_lora, 2}` — **AC 전용** 모델 크기 공유값 (2B / 3-4B / 7-8B). MC 에는 적용되지 않는다.
3. `_MODEL_CONFIG[model].hparam_overrides` — 계열 delta (LLaVA → `weight_decay=0.0`, Gemma-4 → `optim=adamw_torch_fused, seed=3407`).

AC 는 ① → ② → ③ 순으로 `dict.update()` 되며, MC 는 ① → ③ 만 적용된다 (tier 미적용). 각 모델은 `_MODEL_CONFIG[model]["size"]` (`"2B" | "3-4B" | "7-8B"`) 필드로 tier 를 지정한다. MobiBench 는 평가 전용 벤치마크이므로 학습 하이퍼파라미터 해석에서 제외된다.

#### AC 크기 tier 값 (`_SIZE_CONFIG_AC`)

**Stage 1 (full FT)** — dataset baseline 대비 다른 필드만:

| 구간 | lr | warmup_ratio | max_grad_norm |
|---|---|---|---|
| 2B | 1.5e-5 | 0.08 | 0.5 |
| 3-4B | 1.2e-5 | 0.06 | 0.5 |
| 7-8B | (baseline 유지: 1.0e-5 / 0.03 / 1.0) | | |

**Stage 1 LoRA** — `stage1_full` 위에 덮어쓰기:

| 구간 | lr | LoRA r / α | dropout |
|---|---|---|---|
| 2B | 1.5e-4 | 8 / 16 | 0.05 |
| 3-4B | 1.2e-4 | 12 / 24 | 0.05 |
| 7-8B | 1.0e-4 | 16 / 32 | 0.05 |

**Stage 2 (LoRA)** — dataset baseline 대비 다른 필드만:

| 구간 | lr | LoRA r / α | dropout | warmup_ratio |
|---|---|---|---|---|
| 2B | 6.0e-5 | 16 / 32 | 0.05 | 0.05 |
| 3-4B | 5.0e-5 | 24 / 48 | 0.05 | 0.04 |
| 7-8B | 4.0e-5 | (baseline: 32 / 64) | 0.05 | (baseline: 0.03) |

설계 근거: `outputs/AC/eval/qwen{2.5-vl-7b,3-vl-8b}/stage2_eval` 실측에서 lr 5e-5 가 7-8B 상단 경계 (7B e3 retrograde, 8B cond_text 퇴화), dropout 0.10 이 저빈도 action type 을 불안정하게 만듦. 2B / 3-4B 는 Stage 1 크기 규칙을 Stage 2 에 이식한 외삽.

#### 계열 delta (`_MODEL_CONFIG[model].hparam_overrides`)

| 계열 | stage1 / stage2 에 추가 |
|---|---|
| LLaVA (3 모델, 7-8B) | `weight_decay: 0.0` |
| Gemma-4 (2 모델, 2B / 3-4B) | `optim: "adamw_torch_fused", seed: 3407` |
| Qwen 계열 (7 모델) | (empty — 전부 tier 값 그대로) |

#### `gradient_accumulation_steps`

`_DATASET_CONFIG` 상수가 아니라 각 노트북 Section 0 의 CONFIGS 셀에서 런타임 계산된다. 불변식:

```
global_batch = per_device_train_batch_size * gradient_accumulation_steps * NPROC_PER_NODE
            == GLOBAL_BATCH_SIZE  (기본 64)

gradient_accumulation_steps = GLOBAL_BATCH_SIZE / (per_device * NPROC_PER_NODE)
```

`per_device_train_batch_size` 는 프레임워크별 메모리 기준 상수 (LlamaFactory=2, Unsloth=1). `NPROC_PER_NODE` 는 `.env` 에서 읽는 GPU 수 (기본 2). Section 0 CONFIGS 셀이 `_derive_grad_accum()` 으로 역계산해 CONFIGS 의 `stage1.gradient_accumulation_steps` / `stage2.gradient_accumulation_steps` 에 주입하며, 나누어떨어지지 않으면 `ValueError` 로 중단한다 (silent rounding 금지).

## 3. 데이터와 설정 계약

### 데이터 디렉토리

```
data/
├── AndroidControl/                   # 학습 + 평가 (Stage 1 + Stage 2)
│   ├── gui-model_stage1.jsonl
│   ├── gui-model_stage1_train.jsonl
│   ├── gui-model_stage1_test.jsonl
│   ├── gui-model_stage2.jsonl
│   ├── gui-model_stage2_train.jsonl
│   ├── gui-model_stage2_test_id.jsonl      # in-domain (train 에 등장한 앱)
│   ├── gui-model_stage2_test_ood.jsonl     # out-of-domain (train 에 없는 앱)
│   ├── episodes_meta.jsonl                 # primary_app = 전경 앱 package_name (split_data.py 의 입력)
│   └── images/
├── MonkeyCollection/                  # Stage 1 전용 학습 + 평가
│   ├── gui-model_stage1.jsonl              # 약 100K
│   ├── gui-model_stage1_train.jsonl        # split_data.py --dataset MC (95%)
│   ├── gui-model_stage1_test.jsonl         # (5%)
│   └── images/
└── MobiBench/                         # 평가 전용 벤치마크 (단일 파일)
    ├── gui-model_stage1.jsonl               # stage1_eval.sh --eval-datasets MB
    ├── gui-model_stage2.jsonl               # stage2_eval.sh --eval-datasets MB (single-pair overall)
    └── images/
```

- Stage 1 (AC, MC) 은 random split 이다. MC 는 `_STAGE1_ONLY` 로 Stage 2 자동 skip.
- Stage 2 (AC only) 는 **app-level in-domain / out-of-domain split**. 앱 집합(=package 식별자 집합) 을 ID/OOD 로 나눈 뒤 각 풀에서 action type stratified 샘플링 (largest-remainder). train 은 `null` primary_app 에피소드까지 흡수해 regular 크기 유지.
- 메타 추출: `scripts/extract_androidcontrol_metadata.py` (TFRecord → `primary_app` = 전경 application window 의 `package_name`, AndroidAccessibilityForest proto 를 디코드해 각 step 을 집계 후 다수결). `pip install android-env` 필요. MC 는 Stage 2 가 없어 메타 추출 불필요. MB 는 평가 전용이라 split 자체가 없음.
- [`scripts/split_data.py`](./scripts/split_data.py) 가 Stage 1 random split + Stage 2 ID/OOD split 을 담당한다. 기본 크기: AC train 50K / test_id 3K / test_ood 3K. MC 는 Stage 1 random split 만.
- MB 는 `_EVAL_ONLY_BENCHMARKS` 메커니즘으로 notebook dataset_info 등록 셀이 `GUI-Model-MB_stage{1,2}` 단일 파일 entry 를 별도 추가한다.

#### `episodes_meta.jsonl` 스키마 (AC only)

```jsonl
{"episode_id": 0, "goal": "...", "primary_app": "com.zoho.meeting", "actions": ["...", ...], "step_instructions": [...], ...}
```

- AC: `episode_id` 는 **int** (0, 1, 2, ...). 원본 이미지 경로는 zero-padded string (`episode_006881_step_0001.png`). `split_data.py::_norm_ep` 가 `str(int(...))` 로 정규화해 매칭한다.
- `primary_app` 은 각 step 의 `accessibility_trees` (`AndroidAccessibilityForest` proto) 에서 전경 `TYPE_APPLICATION` window 의 root `package_name` 을 뽑아 에피소드 전체에서 다수결로 정한 값. 앱 라벨이 아닌 **package 식별자** (예: `com.zoho.meeting`, `com.ajnsnewmedia.kitchenstories`). 첫 액션이 `open_app` 이 아니어도 채워진다.
- 시스템/런처 package (`com.google.android.apps.nexuslauncher`, `com.android.launcher3`, `com.android.systemui` 등) 는 다수결에서 제외되지만, 모든 step 이 그 범주에만 머무른 에피소드에서는 fallback 으로 포함될 수 있다.
- 전경 window 를 뽑지 못한 드문 경우 `primary_app` 은 `None` 이며, 해당 에피소드는 train 풀에만 합류하고 test 분할에서 제외된다 (`--stage2-exclude-null-app` 으로 완전 제외 가능).
- MC 는 Stage 2 가 없어 episodes_meta 를 필요로 하지 않는다. MB 는 평가 전용이라 split 자체가 없으므로 episodes_meta 가 없다.

### 데이터셋 이름 규약

| 용도 | AndroidControl | MonkeyCollection | MobiBench (eval-only) |
|------|----------------|-------------------|------------------------|
| `data/` 아래 실제 디렉토리 | `AndroidControl` | `MonkeyCollection` | `MobiBench` |
| shell script 단축 코드 | `AC` | `MC` | `MB` (eval 전용) |
| LLaMA-Factory dataset prefix | `GUI-Model-AC` | `GUI-Model-MC` | `GUI-Model-MB` (stage{1,2} 단일) |
| `outputs/` 하위 최상위 디렉토리 | `AC` | `MC` | — (평가 결과는 TRAIN_DS/on-MB/) |
| Stage 2 지원 | ✓ | ✗ (`_STAGE1_ONLY`) | ✓ (single-pair overall) |

### LLaMA-Factory 등록

- `gui-model-llamafactory.ipynb` 의 Section 1-2 가 `LlamaFactory/data/dataset_info.json` 를 갱신한다. Unsloth 노트북에는 이 셀이 없다 (Unsloth 는 JSONL 직접 로드).
- JSONL 파일 경로는 `../../data/{DATASET_NAME}/...` 형태의 상대 경로로 등록된다.
- JSONL 내부 `images` 값은 `{DATASET_NAME}/images/...` 형태의 상대 경로를 유지한다.
- `vllm_infer.py` 호출 시 `--dataset_dir`에 **절대 경로** (`$LF_ROOT/data`)를 전달해야 한다. 상대 경로(`"data"`)를 사용하면 HF datasets 캐시가 다른 cwd 에서 생성된 미해석 이미지 경로를 재사용하여 `FileNotFoundError`가 발생할 수 있다.

## 4. 파이프라인 컴포넌트

### 로컬 오케스트레이션 레이어

- [`gui-model-llamafactory.ipynb`](./gui-model-llamafactory.ipynb) · [`gui-model-unsloth.ipynb`](./gui-model-unsloth.ipynb): 백엔드별 전체 실험 실행의 기준 경로
- [`scripts/_common.sh`](./scripts/_common.sh): 공통 path, `.env`, dataset 매핑, 모델 레지스트리, bash 4+ 가드, logging
- [`scripts/split_data.py`](./scripts/split_data.py): split 및 AndroidControl Stage 2 subsample
- [`scripts/_hungarian_eval.py`](./scripts/_hungarian_eval.py): Stage 1 metric 집계 (`score` 서브커맨드)
- [`scripts/_action_eval.py`](./scripts/_action_eval.py): Stage 2 metric 집계, ID/OOD/overall 3 섹션 산출 (`score` 서브커맨드)

### Stage 1 automation

Stage 1 은 `--stage1-mode {full|lora}` 로 finetuning 방식을 선택한다 (기본: `full`). 모드별로 YAML 경로 · adapter 경로 · merged 경로 · HF Hub ID 가 모두 접미사로 분리되어 공존한다.

- [`scripts/stage1_train.sh`](./scripts/stage1_train.sh)
  - `backend=llamafactory`: `examples/custom/GUI-Model-{DS}/stage1_${MODE}/{MODEL}_world-model.yaml`, `FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=${NPROC_PER_NODE}`
  - `backend=unsloth`: `unsloth/configs/GUI-Model-{DS}/stage1_${MODE}/{MODEL}_world-model.yaml`, `accelerate launch --multi_gpu --num_processes ${NPROC_PER_NODE} scripts/_unsloth_train.py`
  - LF full YAML 은 `finetuning_type: full`, LF lora YAML 은 `finetuning_type: lora` + `lora_rank/alpha/target/dropout` 블록을 포함한다.
  - `NPROC_PER_NODE` 는 `.env` 에서 관리 (기본값 2). 각 노트북의 Section 0 CONFIGS 셀이 이 값을 읽어 각 YAML 의 `gradient_accumulation_steps` 를 역계산하므로, GPU 수 변경 시 `.env` 수정 후 Section 0 의 CONFIGS 셀 + Stage 1/2 YAML 생성 셀을 재실행해야 한다.
- [`scripts/stage1_merge.sh`](./scripts/stage1_merge.sh)
  - 표준 실행 순서: **train → merge → eval**. merge 가 eval 에 선행하며, `BEST_CHECKPOINT` 의존은 사라졌다.
  - `outputs/{DS}/adapters/{MODEL}_stage1_${MODE}/checkpoint-*` 전수 loop. 각 ckpt 에서 `trainer_state.json` 의 `epoch` 을 `int(round(...))` 로 추출.
  - `backend=llamafactory`:
    - full 모드: 임시 merge YAML 의 `model_name_or_path` 를 해당 checkpoint 로 설정 → `llamafactory-cli export`
    - lora 모드: `model_name_or_path: {base_model}` + `adapter_name_or_path: {ckpt}` + `finetuning_type: lora` 블록 삽입
  - `backend=unsloth`: `scripts/_unsloth_merge.py --mode {full|lora}` (기존 시그니처 무변경, 쉘이 N 회 호출)
  - 산출물 (epoch 별): `outputs/{DS}/merged/{MODEL}_stage1_${MODE}/epoch-{E}/` + HF Hub push `SaFD-00/...stage1-${MODE}-world-model-epoch{E}`. HF repo id 는 `_common.sh::hf_repo_id_stage1` 단일 정의.
- [`scripts/stage1_eval.sh`](./scripts/stage1_eval.sh)
  - Phase A (baseline zero-shot, mode 무관) + Phase B (`--epochs` 플래그로 받은 정수 리스트를 따라 **HF Hub merged repo sweep**, 기본 `1,2,3`).
    - `vllm_infer.py --model_name_or_path <HF repo id>` 만 전달 (merged 이므로 adapter 인자 · `max_lora_rank` 불필요; full/lora 공통). 로컬 `adapters/.../checkpoint-*` 는 조회하지 않으므로 학습 머신이 아닌 환경에서도 재평가 가능.
  - 결과 경로: `outputs/{DS}/eval/{MODEL}/stage1_eval/{base, ${MODE}_world_model/epoch-{E}}/`
  - 각 sweep 결과에 `_hungarian_eval.py score` 가 호출되어 `hungarian_metrics.json` 을 저장한다. **Winner 자동 선정은 없다** — 사용자가 결과를 보고 Stage 2 에 쓸 epoch 을 `--stage1-epoch` 로 직접 지정.
  - **재실행 시 skip**: marker `hungarian_metrics.json` 존재 unit 은 건너뛴다. 강제 재평가는 해당 marker 를 `rm` 한 뒤 재실행.
  - `--variants` 로 특정 variant 만 (예: `--variants base,full_world_model`) 평가 가능.

### Stage 2 automation

Stage 2 스크립트는 `--stage2-mode {full|lora}` (기본 lora) 로 학습 방식, `--stage1-mode {full|lora}` + `--stage1-epoch N` 으로 world-model variant 의 상류 소스를 결정한다. base variant 는 Stage 1 무관.

- [`scripts/stage2_train.sh`](./scripts/stage2_train.sh)
  - YAML: `{LF|Unsloth}/stage2_${STAGE2_MODE}/{MODEL}_{base,world-model-full,world-model-lora}.yaml` (각 노트북 Section 0 의 "Stage 2 YAML 일괄 생성" 셀이 해당 백엔드 모델만 생성)
  - world-model variant 는 `--stage1-epoch N` 으로 지정된 local `merged/{M}_stage1_${STAGE1_MODE}/epoch-${N}/` 을 base 로 사용 (YAML `model_name_or_path` 런타임 sed 치환). Local merge 디렉토리 미존재 시 hard-fail.
  - backend 분기 (llamafactory CLI vs `scripts/_unsloth_train.py`) 동일.
- [`scripts/stage2_merge.sh`](./scripts/stage2_merge.sh)
  - 각 variant 의 `adapters/{M}_stage2_${STAGE2_MODE}_{base|world_model_from_${STAGE1_MODE}}/checkpoint-*` 전수 loop.
  - Full FT: checkpoint 자체가 전체 모델 → merge YAML 의 `model_name_or_path` 에 직접 전달 (adapter 블록 없음).
  - LoRA: `model_name_or_path: {base}` + `adapter_name_or_path: {ckpt}` + `finetuning_type: lora`.
  - HF 네이밍 (`_common.sh`):
    - base: `hf_repo_id_stage2_base(MODEL, DS, STAGE2_MODE, E2)` → `...base-stage2-{M2}-epoch{E2}`
    - world-model: `hf_repo_id_stage2_world_model(MODEL, DS, STAGE1_MODE, STAGE1_EPOCH, STAGE2_MODE, E2)` → `...world-model-stage1-{M1}-epoch{E1}-stage2-{M2}-epoch{E2}`
- [`scripts/stage2_eval.sh`](./scripts/stage2_eval.sh)
  - `--variants` 로 `base`, `{full|lora}_base`, `{full|lora}_world_model` 중 선택 평가. world-model variant 는 `--stage1-epoch` 로 HF 레포 계보 번호를 주입한다.
  - `--train-dataset AC` (현재 유일한 학습 DS. MC 는 Stage 2 데이터 없어 거절) + `--eval-datasets LIST` (AC, MB). EVAL_DS 별 분기:
    - **EVAL_DS=AC**: ID + OOD 두 test 파일을 함께 추론해 `_action_eval.py score --test-id ... --pred-id ... --test-ood ... --pred-ood ...` 가 **overall / in_domain / out_of_domain** 3-섹션 기록.
    - **EVAL_DS=MB**: 단일 파일 `gui-model_stage2.jsonl` 1 회 추론 후 `_action_eval.py score --test ... --pred ...` single-pair 모드 → `overall` 1-섹션만 기록.
  - 결과 경로: `outputs/{TRAIN_DS}/eval/{MODEL}/stage2_eval/{variant}[_from_{M1}_ep{E1}]/epoch-{E2}/on-{EVAL_DS}/`
  - **재실행 시 skip**: marker `action_metrics.json` 존재 unit 은 variant × EVAL_DS 조합 별로 독립 skip.

### Shell script CLI

```bash
# 학습/merge — --dataset 는 {AC|MC|all}. MB 는 평가 전용이라 거절.
bash scripts/stage1_train.sh --model qwen3-vl-8b --dataset AC                        # full (default)
bash scripts/stage1_merge.sh --model qwen3-vl-8b --dataset AC                        # 전 epoch push
bash scripts/stage1_train.sh --model gemma-4-e2b --dataset MC --stage1-mode lora
bash scripts/stage2_train.sh --model qwen3-vl-8b --dataset AC \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora
bash scripts/stage2_merge.sh --model qwen3-vl-8b --dataset AC \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora

# 평가 — --train-dataset 로 HF repo 를, --eval-datasets 로 test 셋을 지정 (교차 평가).
bash scripts/stage1_eval.sh  --model qwen3-vl-8b --train-dataset AC --eval-datasets AC,MC,MB \
     --epochs 1,2,3
bash scripts/stage2_eval.sh  --model qwen3-vl-8b --train-dataset AC --eval-datasets AC,MB \
     --stage1-mode full --stage1-epoch 3 --stage2-mode lora \
     --variants base,lora_base,lora_world_model --epochs 1,2,3
```

플래그:

**학습/merge (`stage{1,2}_{train,merge}.sh`)**:
- `--dataset DS`: `AC|MC|all` (기본 all=AC,MC). **MB 는 거절됨**.
- `--stage1-mode {full|lora}` (기본 full)
- `--stage2-mode {full|lora}` (기본 lora, stage2 스크립트 전용)
- `--stage1-epoch N` (stage2 world-model variant 에서 상류 epoch 지정)

**평가 (`stage{1,2}_eval.sh`)**:
- `--train-dataset {AC|MC}` (필수) — HF repo 를 식별할 학습 DS. Stage 2 eval 은 AC 만.
- `--eval-datasets LIST` (기본: `--train-dataset` 단일값. 허용: `AC,MC,MB`) — test JSONL 을 로드할 DS.
- `--epochs LIST` (콤마 구분, 기본 `1,2,3`)
- `--variants LIST`

## 5. 실행 데이터 흐름

```
raw JSONL + screenshots  (AC: train+eval, MC: Stage1 전용, MB: eval-only 단일 파일)
  -> extract_androidcontrol_metadata.py  (AC 만 — primary_app = 전경 앱 package_name, accessibility_trees proto 기반)
  -> split_data.py                    (AC: Stage1 random + Stage2 ID/OOD | MC: Stage1 random only)
  -> dataset_info.json registration   (AC/MC: train+test | MB: eval-only 단일 entry)
  -> [per model] Stage 1 train  (mode1 = full | lora, 학습 DS ∈ {AC, MC})
       → adapters/{M}_stage1_{mode1}/checkpoint-*/
  -> [per model] Stage 1 merge (모든 epoch 각각)
       → merged/{M}_stage1_{mode1}/epoch-{E1}/   +   HF Hub ...world-model-stage1-{mode1}-epoch{E1}
  -> [per model] Stage 1 eval (HF Hub sweep × cross-dataset)
       → eval/{TRAIN_DS}/{M}/stage1_eval/{mode1}_world_model/epoch-{E1}/on-{EVAL_DS}/hungarian_metrics.json
       (EVAL_DS ∈ {AC, MC, MB} — MB 는 단일 파일 stage1.jsonl, AC/MC 는 *_test.jsonl)
       (user picks an epoch E1 → passes as --stage1-epoch to Stage 2)
  -> [per model] Stage 2 train  (mode2 = full | lora,  variant ∈ {base, world-model-{mode1}})
       world-model base = merged/{M}_stage1_{mode1}/epoch-{E1}/   (local, from --stage1-epoch)
       → adapters/{M}_stage2_{mode2}_{base|world_model_from_{mode1}}/checkpoint-*/
  -> [per model] Stage 2 merge (variant × 전 epoch)
       → merged/{M}_stage2_{mode2}_{variant}/epoch-{E2}/
       + HF Hub:
          base       : ...base-stage2-{mode2}-epoch{E2}
          world-model: ...world-model-stage1-{mode1}-epoch{E1}-stage2-{mode2}-epoch{E2}
  -> [per model] Stage 2 eval (HF Hub sweep × cross-dataset)
       → eval/{TRAIN_DS}/{M}/stage2_eval/.../epoch-{E2}/on-{EVAL_DS}/action_metrics.json
          EVAL_DS=AC: { overall, in_domain, out_of_domain }  (test_id + test_ood)
          EVAL_DS=MB: { overall }                             (gui-model_stage2.jsonl single-pair)
```

### 산출물 위치

모든 산출물은 `GUI-Model/outputs/` 단일 루트 아래에 **데이터셋 중심 + category 분리** 구조로 모인다. merged/eval 은 `epoch-{E}/` 서브디렉토리로 epoch 별 분리. Stage 1 full/lora 산출물은 경로 접미사로 분리되어 공존 가능하다.

```
GUI-Model/outputs/{DS}/
├── adapters/
│   ├── {model}_stage1_{full,lora}/                                        # Stage 1 체크포인트
│   ├── {model}_stage2_{full,lora}_base/                                   # Stage 2 base
│   └── {model}_stage2_{full,lora}_world_model_from_{full,lora}/           # Stage 2 world-model
├── eval/{model}/
│   ├── stage1_eval/
│   │   ├── base/
│   │   ├── full_world_model/epoch-{E}/
│   │   └── lora_world_model/epoch-{E}/
│   └── stage2_eval/
│       ├── base/
│       ├── {full,lora}_base/epoch-{E}/
│       └── {full,lora}_world_model_from_{full,lora}_ep{E1}/epoch-{E2}/
└── merged/
    ├── {model}_stage1_{full,lora}/epoch-{E}/
    ├── {model}_stage2_{full,lora}_base/epoch-{E}/
    └── {model}_stage2_{full,lora}_world_model_from_{full,lora}/epoch-{E}/
```

`BEST_CHECKPOINT` / `BEST_CHECKPOINT.json` 파일은 더 이상 생성되지 않는다.

### HuggingFace 업로드 ID 패턴 (epoch 별 개별 repo)

| Stage / variant | 패턴 |
|-------|------|
| Stage 1 (full FT) | `SaFD-00/{short}-{slug}world-model-stage1-full-epoch{E}` |
| Stage 1 (LoRA)    | `SaFD-00/{short}-{slug}world-model-stage1-lora-epoch{E}` |
| Stage 2 base      | `SaFD-00/{short}-{slug}base-stage2-{M2}-epoch{E2}`  (`M2` ∈ {full, lora}) |
| Stage 2 world     | `SaFD-00/{short}-{slug}world-model-stage1-{M1}-epoch{E1}-stage2-{M2}-epoch{E2}` |

`{slug}` 는 `ac-` (AndroidControl) 또는 `mc-` (MonkeyCollection). MB slug `mb-` 는 MB 를 학습 대상으로 쓰지 않는 현재 파이프라인에서 dormant 이다 (새로 push 되지 않음). `{E}` 는 각 `checkpoint-*/trainer_state.json` 의 `epoch` 을 `int(round(...))` 로 추출한 값. 조립은 `scripts/_common.sh::hf_repo_id_stage1` / `hf_repo_id_stage2_base` / `hf_repo_id_stage2_world_model` 에 중앙화.

## 6. 메트릭

자동 winner 선정은 없다. Stage 1/2 모두 `score` 서브커맨드로 평가 결과를 JSON 으로 저장하고, 사용자가 결과를 보고 Stage 2 에 쓸 Stage 1 epoch 을 `--stage1-epoch` 로 지정한다.

### Stage 1

- baseline: zero-shot (variant `base`)
- 변형: `full_world_model`, `lora_world_model`
- metric: `avg_hungarian_f1`, `avg_bleu`, `avg_rouge_l` 등
- 저장: `outputs/{DS}/eval/{MODEL}/stage1_eval/{variant}[/epoch-{E}]/hungarian_metrics.json`

### Stage 2

- baseline: zero-shot (variant `base`)
- 변형: `{full|lora}_base`, `{full|lora}_world_model` (world-model 은 `--stage1-epoch` 로 상류 epoch 지정)
- 평가 파일: `gui-model_stage2_test_id.jsonl` + `gui-model_stage2_test_ood.jsonl` (AC/MB 모두 `split_data.py` 로 생성)
- metric (3 섹션): `action_metrics.json` 내부 `overall` / `in_domain` / `out_of_domain` 각각에 `step_accuracy`, `macro_step_accuracy`, `parse_rate`, `type_accuracy`, `cond_{index,dir,app,text}_acc`, `per_type[]` 포함.

#### `action_metrics.json` 스키마 예시

```json
{
  "overall": {
    "total": 6000,
    "parse_rate": 0.97,
    "type_accuracy": 0.81,
    "step_accuracy": 0.63,
    "macro_step_accuracy": 0.55,
    "cond_index_acc": 0.62,
    "cond_dir_acc": 0.73,
    "cond_app_acc": 0.59,
    "cond_text_acc": 0.48,
    "per_type": {
      "click":    {"count": 3337, "type_acc": 0.89, "step_acc": 0.61},
      "scroll":   {"count": 708,  "type_acc": 0.92, "step_acc": 0.73},
      "open_app": {"count": 365,  "type_acc": 0.78, "step_acc": 0.59},
      "input":    {"count": 401,  "type_acc": 0.71, "step_acc": 0.48},
      "finish":   {"count": 987,  "type_acc": 0.72, "step_acc": 0.72}
    }
  },
  "in_domain":     { "total": 3000, "step_accuracy": 0.68, "...": "..." },
  "out_of_domain": { "total": 3000, "step_accuracy": 0.58, "...": "..." }
}
```

`overall` 은 id + ood 를 단순 concat 해 재집계한 결과이므로 `overall.total == in_domain.total + out_of_domain.total` 이 항상 성립한다. ID/OOD gap (`in_domain.step_accuracy - out_of_domain.step_accuracy`) 이 앱 일반화 정도를 나타낸다.

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

`action_metrics.json` 각 섹션의 키:
- 1차: `step_accuracy`
- 보조: `macro_step_accuracy` (7 type 평균), `parse_rate`, `type_accuracy`,
  `cond_index_acc` / `cond_dir_acc` / `cond_app_acc` / `cond_text_acc`,
  `per_type[t] = {count, type_acc, step_acc}`

Reference baselines (해석용):
- `type` random baseline: 1/7 ≈ 14.3%
- `scroll` majority baseline (`down`): 79.0%
- `finish.status` constant baseline: 100% (해석 무의미)

정본은 `scripts/_action_eval.py` 이며, 두 노트북의 Section 8 "Stage 2 평가" 정본 셀이 이 파일과 글자 단위 동치를 유지한다.
회귀 테스트는 `tests/test_action_eval.py` (48 케이스 — parse_action / evaluate_single / evaluate_pairs 분기, unknown type 집계, `cond_*` n=0, `predict`/`output` fallback, ID+OOD 통합 집계).

## 7. 중요한 운영 제약

- `gui_model/` 패키지에는 핵심 파이프라인이 없다. 변경 작업은 notebook, shell script, custom YAML 경로를 우선 검토해야 한다.
- merge 스크립트는 `outputs/{DS}/adapters/.../checkpoint-*` 가 하나라도 없으면 hard-fail (전 epoch loop).
- Stage 2 train/merge (world-model variant) 는 `--stage1-epoch N` 으로 지정된 로컬 `outputs/{DS}/merged/{MODEL}_stage1_{full|lora}/epoch-${N}/` 이 반드시 선행돼야 한다 (stage1_train → stage1_merge). Stage 2 eval 은 HF Hub merged repo 만 pull 하며 `--stage1-epoch` 값을 HF 레포명 계보 번호로 주입한다.
- merge/eval 스크립트는 `.env` 또는 환경변수의 `HF_TOKEN` (HF Hub push/pull 용) 과 Python `pyyaml` 을 전제로 한다.
- shell automation 은 bash 4+ 환경을 요구한다.
- 모델 추가 시 backend 에 맞는 노트북 (`gui-model-llamafactory.ipynb` 또는 `gui-model-unsloth.ipynb`) 의 `_MODEL_CONFIG` 와 `_common.sh` `MODEL_ID`/`MODEL_TEMPLATE`/`ALL_MODELS` 를 동시에 동기화해야 한다. backend 가 기본값(`llamafactory`)이 아니면 `MODEL_BACKEND` 도 동기화한다.
- `backend=unsloth` 모델 (Gemma-4 계열) 은 `unsloth/configs/GUI-Model-{DS}/stage{1,2}_*/...` YAML 을 사용하며, `gui-model-unsloth.ipynb` Section 0 의 "Unsloth Stage {1,2} YAML 일괄 생성" 셀에서 자동 생성된다. Unsloth YAML 은 `cwd=UNSLOTH_ROOT` 기준 `../data/`, `../outputs/` 상대경로를 쓴다. LF YAML 은 `gui-model-llamafactory.ipynb` 에서 별도로 생성된다.
- Unsloth 체크포인트는 HF 표준 safetensors, LoRA adapter 는 PEFT 표준이므로 `vllm_infer.py` 가 backend 독립적으로 로드한다.
- `scripts/_unsloth_train.py` 는 모듈 최상단에서 `import unsloth` 를 선행시키고 `UNSLOTH_RETURN_LOGITS=1` 을 설정한다. trl ≥ 0.24 의 `SFTTrainer.compute_loss` 가 `entropy_from_logits(outputs.logits)` 를 직접 호출하기 때문이며, Unsloth 기본값(EMPTY_LOGITS) 과 충돌하면 `TypeError: 'function' object is not subscriptable` 로 첫 step 에서 실패한다.
- **transformers 버전 (env 별로 다르다)**: `pyproject.toml [project.optional-dependencies]` 와 `setup.py::EXTRAS` 에서 — llamafactory extras 는 `transformers==5.5.4` 로 **고정** (Qwen / LLaVA 검증값), unsloth extras 는 `transformers>=5.5.4` 로 **상한 해제** (학습 대상 google/gemma-4-E2B-it · google/gemma-4-E4B-it 의 modeling 코드가 최신 Gemma-4 loader 를 요구하기 때문). 서브프로젝트(`unsloth/` `<=5.5.0`, `LlamaFactory/` `<=5.2.0`) transitive 상한과 충돌 시 README "환경 설치 > PIP_USER 플래그 / 전제" 의 회피 명령(`pip install --upgrade transformers` 또는 서브프로젝트 `--no-deps` 재설치) 을 사용한다. 서브프로젝트 `pyproject.toml` 은 수정하지 않는다.
- trl 0.24 / transformers 5.x API 매핑: `SFTConfig(max_length=...)`, `SFTTrainer(processing_class=...)` 를 사용한다. 구버전 키(`max_seq_length`, `tokenizer=`, `overwrite_output_dir`) 는 `TypeError` 를 낸다.
- **Unsloth env 에는 deepspeed 가 설치되지 않는다**: `setup.py::EXTRAS["unsloth"]` 가 deepspeed 를 의존성에서 뺐다. FastModel 의 메모리 최적화 / gradient checkpointing 이 deepspeed ZeRO 와 충돌하고, env 에 deepspeed 가 있으면 `accelerate launch` 가 deepspeed plugin 을 자동 활성화해 첫 step 에서 실패하기 때문이다. 따라서 이전 단일 env 시절의 `pip uninstall -y deepspeed` 토글 단계는 사라졌다. 반대로 `gui-model-llamafactory` env 는 `deepspeed>=0.10.0,<=0.18.4` 가 기본 포함된다.
- `scripts/_unsloth_train.py` 의 Unsloth Full FT 권장 사양 매핑 (Gemma-4 e2b/e4b stage1):
  - 모델 로드 시 `FastModel.from_pretrained(load_in_16bit=True, use_gradient_checkpointing="unsloth", full_finetuning=True)` 로 호출 → YAML `load_in_16bit`, `gradient_checkpointing` 키가 그대로 전달된다.
  - `gradient_checkpointing` 은 모델 로드 단계에서만 적용하며 `SFTConfig` 에는 전달하지 않는다 (이중 적용 방지).
  - 모델 로드 직후 `get_chat_template(tokenizer, cfg["template"])` 호출 → YAML `template: gemma4` 가 실제 chat token 정합성에 반영된다.
  - Full FT 분기에서 `freeze_vision_tower: true` 면 `vision_tower|vision_model|visual|image_encoder` 키워드를 포함한 named parameter 의 `requires_grad=False` 처리 후 frozen 텐서 수/파라미터 수를 stderr 로 출력한다.
  - `SFTConfig.optim` 은 YAML `optim` (기본 `adamw_torch_fused`) 을 사용한다. Gemma-4 e2b/e4b 는 stage1·stage2 override 에서 `adamw_torch_fused` 를 명시하며, multi-GPU DDP 환경에서는 `ddp_find_unused_parameters: true` 를 함께 사용해 frozen vision tower 로 인한 unused-grad 경고를 피한다.
