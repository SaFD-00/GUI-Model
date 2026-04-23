# AGENTS.md

`GUI-Model/` 하위 프로젝트에서 작업하는 에이전트를 위한 가이드다.

## 현재 코드 기준 요약

- 이 프로젝트의 실제 실행 엔트리포인트는 **백엔드별로 분리된 두 노트북** [`gui-model-llamafactory.ipynb`](./gui-model-llamafactory.ipynb) · [`gui-model-unsloth.ipynb`](./gui-model-unsloth.ipynb) 와 [`scripts/`](./scripts) 다. 각 노트북은 **conda env 한 개씩** 을 전제로 한다 (`gui-model-llamafactory` / `gui-model-unsloth`, `pip install -e '.[llamafactory|unsloth]'`).
- 12개 Vision-Language 모델(Qwen2-VL, Qwen2.5-VL, Qwen3-VL, Gemma-4, LLaVA 계열)을 지원한다.
- 모델 레지스트리는 세 곳에 있다: LF 노트북 Section 0 의 `_MODEL_CONFIG` (10 LF 모델), Unsloth 노트북 Section 0 의 `_MODEL_CONFIG` (2 Gemma-4 모델), `scripts/_common.sh` 의 `MODEL_ID`/`MODEL_TEMPLATE`/`MODEL_BACKEND` (12개 전부).
- 학습/export 는 env 에 설치된 backend 가 수행한다 (`scripts/_common.sh::MODEL_BACKEND` 매핑은 shell 단계의 분기용으로 유지되지만, env 경계를 넘는 호출은 ImportError 로 실패한다):
  - `gui-model-llamafactory` env (Qwen/LLaVA 계열): 저장소 내부 [`LlamaFactory/`](./LlamaFactory) clone + `llamafactory-cli`.
  - `gui-model-unsloth` env (Gemma-4 계열): 저장소 내부 [`unsloth/`](./unsloth) clone + `scripts/_unsloth_{train,merge}.py`. **이 env 에는 deepspeed 가 설치되지 않는다** (FastModel / deepspeed ZeRO 충돌).
- 평가 (`scripts/stage{1,2}_eval.sh`) 는 backend 독립이다. `vllm_infer.py` 가 HF 표준 safetensors / PEFT adapter 를 그대로 로드.
- [`gui_model/`](./gui_model) 패키지는 사실상 배포용 스텁이며, 핵심 파이프라인 로직은 여기에 없다.
- **데이터셋 역할 분리**:
  - 학습 대상 DS 는 `AndroidControl` (AC) 과 `MonkeyCollection` (MC).
  - `MobiBench` (MB) 는 **평가 전용 벤치마크**. 학습/merge 스크립트에서 `--dataset MB` 는 `scripts/_common.sh::parse_args` 에서 거절된다.
  - MC 는 Stage 1 전용 — Stage 2 파이프라인에서는 `_STAGE1_ONLY` guard 로 skip.
  - 평가 스크립트는 `--train-dataset {AC|MC}` + `--eval-datasets AC,MC,MB` 로 학습 DS 와 평가 DS 를 분리한다.

## 어디를 수정해야 하는가

- **모델 추가**: backend 에 따라 노트북을 먼저 선택한다 (`backend=llamafactory` → `gui-model-llamafactory.ipynb`, `backend=unsloth` → `gui-model-unsloth.ipynb`). 선택한 노트북의 Section 0 `_MODEL_CONFIG` (backend 필드 + **`size` 필드 필수**: `"2B" | "3-4B" | "7-8B"`) + `scripts/_common.sh` 의 `MODEL_ID`, `MODEL_TEMPLATE`, `ALL_MODELS` 를 동시에 수정한다. backend 가 기본값(`llamafactory`) 이 아니면 `_common.sh::MODEL_BACKEND` 도 등록. 각 노트북은 Section 0 의 "Stage 1 YAML 일괄 생성" 셀이 자기 backend 용 Stage 1 YAML 을 **full / lora 두 벌** 자동 생성하고, "Stage 2 YAML 일괄 생성" 셀이 Stage 2 YAML 을 **full / lora 두 벌** × base/world-model-full/world-model-lora 자동 생성한다. shell 스크립트의 `--stage1-mode`, `--stage2-mode` 로 full/lora 분기.
- **하이퍼파라미터 구조**: AC 는 `_SIZE_CONFIG_AC[size].stage{1, 1_lora, 2}` 로 **크기 3 단(2B / 3-4B / 7-8B)** 공유값을 관리한다. `_MODEL_CONFIG[model].hparam_overrides` 는 이제 **계열 delta 전용**이다 — LLaVA (`weight_decay: 0.0`), Gemma-4 (`optim: "adamw_torch_fused", seed: 3407`). lr / warmup / LoRA rank / dropout 은 `_MODEL_CONFIG` 에 직접 쓰지 말고 `_SIZE_CONFIG_AC` 에서 해당 tier 의 값을 바꿔야 한다. MC 는 tier 미적용 — dataset baseline + per-model override 만 적용. MB 는 평가 전용이라 학습 하이퍼파라미터 해석에서 제외. merge 순서: `_DATASET_CONFIG` baseline → `_SIZE_CONFIG_AC[size]` (AC 일 때만) → `hparam_overrides`. 전체 표는 [`ARCHITECTURE.md`](./ARCHITECTURE.md) §2 참조.
- **Backend 변경**: 기존 모델을 다른 backend 로 옮길 때는 `_common.sh::MODEL_BACKEND` 한 줄만 수정하면 된다. 단, 해당 backend 용 YAML 이 준비되어 있어야 한다.
- **notebook 실행 순서나 YAML 생성 흐름**: 두 노트북([`gui-model-llamafactory.ipynb`](./gui-model-llamafactory.ipynb), [`gui-model-unsloth.ipynb`](./gui-model-unsloth.ipynb)) 과 [`scripts/stage1_*.sh`](./scripts/stage1_train.sh), [`scripts/stage2_*.sh`](./scripts/stage2_train.sh) 를 함께 맞춰라. 두 노트북은 섹션 번호를 1:1 로 유지해야 한다.
- **데이터 분할 규칙**: [`scripts/split_data.py`](./scripts/split_data.py) 가 기준이다.
- **Stage 1 평가**: [`scripts/_hungarian_eval.py`](./scripts/_hungarian_eval.py) 가 기준 (`score` 서브커맨드만 유지, winner 선정 없음). 흐름은 **train → merge → eval** — `stage1_merge.sh` 가 모든 epoch 를 각각 local merge + HF push 하고 (`trainer_state.json.epoch` 파싱), `stage1_eval.sh` 가 `--train-dataset {AC|MC}` / `--eval-datasets {AC,MC,MB}` / `--variants` / `--epochs` 로 지정된 HF Hub merged repo (`SaFD-00/{short}-{slug}world-model-stage1-{MODE}-epoch{E}`) 를 pull 해 EVAL_DS 별 test JSONL 에 대해 `hungarian_metrics.json` 을 산출한다. EVAL_DS=MB 는 단일 파일 `gui-model_stage1.jsonl`, AC/MC 는 `*_test.jsonl` 을 사용. 산출 경로는 `outputs/{TRAIN_DS}/eval/{MODEL}/stage1_eval/{variant}[/epoch-{E}]/on-{EVAL_DS}/`. 어떤 epoch 을 쓸지는 사용자가 결과를 보고 수동 결정한다. 재실행 시 marker (`hungarian_metrics.json`) 존재 unit 은 skip. 정본은 notebook Section 5.
- **Stage 2 평가**: [`scripts/_action_eval.py`](./scripts/_action_eval.py) 가 기준. `score` 서브커맨드만 유지 (single-pair / ID+OOD 모드 모두 제공), winner/`BEST_CHECKPOINT` 개념 제거. 흐름은 `stage2_train.sh` → `stage2_merge.sh` → `stage2_eval.sh`. TRAIN_DATASET 은 현재 AC 만 지원 (MC 는 Stage 2 데이터 없음). EVAL_DS 별 분기:
  - **EVAL_DS=AC**: ID + OOD 두 test 파일 (`gui-model_stage2_test_{id,ood}.jsonl`) 을 함께 추론 → `action_metrics.json` 에 `overall` / `in_domain` / `out_of_domain` 3 섹션 기록.
  - **EVAL_DS=MB**: 단일 파일 `gui-model_stage2.jsonl` 1-회 추론 → single-pair 모드로 `action_metrics.json` 에 `overall` 1 섹션만 기록.
  HF 네이밍: base variant `SaFD-00/{short}-{slug}base-stage2-{MODE2}-epoch{E2}`, world-model variant `SaFD-00/{short}-{slug}world-model-stage1-{MODE1}-epoch{E1}-stage2-{MODE2}-epoch{E2}`. 산출 경로는 `outputs/{TRAIN_DS}/eval/{MODEL}/stage2_eval/{variant}[/epoch-{E2}]/on-{EVAL_DS}/`. Stage 2 world-model train/merge 는 `--stage1-epoch N` 으로 지정된 로컬 `outputs/{DS}/merged/{MODEL}_stage1_${MODE1}/epoch-${N}/` 를 base 로 사용. 재실행 시 marker (`action_metrics.json`) 존재 unit 은 variant × EVAL_DS 조합 별로 독립 skip. 회귀 테스트 [`tests/test_action_eval.py`](./tests/test_action_eval.py) (48 케이스 — parse / field_match / 집계 + ID+OOD aggregation + single-pair overall). 메트릭 정의는 [`ARCHITECTURE.md`](./ARCHITECTURE.md) §6 참고.
- **shell 실행 공통 규약**: `AC`/`MC`/`MB` 매핑 (`DS_PREFIX` / `HF_SLUG` / `DS_DATADIR`), 모델 레지스트리 → [`scripts/_common.sh`](./scripts/_common.sh). 학습/merge 스크립트는 `parse_args`, 평가 스크립트는 `parse_eval_args` (`--train-dataset` + `--eval-datasets`).
- **Python 의존성**: [`setup.py`](./setup.py) 가 실제 설치 기준.

## 작업 시 주의점

- `LlamaFactory/`, `unsloth/` 내부 파일은 마지막 수단으로만 수정하라. 가능하면 notebook, local shell script, custom YAML (`LlamaFactory/examples/custom/...` 또는 `unsloth/configs/...`), 평가 helper 로 해결한다.
- trl / transformers / peft 의 API 변경(예: trl 0.24 의 `max_length`·`processing_class`, transformers 5.x 의 `overwrite_output_dir` 제거)에 대응할 때는 `scripts/_unsloth_train.py` 내부에서만 호환 계층을 유지하고, `unsloth/` 하위는 건드리지 않는다.
- transformers 버전을 바꿀 때는 [`pyproject.toml`](./pyproject.toml) `[project.optional-dependencies].llamafactory` · `.unsloth` 두 블록과 [`setup.py`](./setup.py) `EXTRAS["llamafactory"]` · `EXTRAS["unsloth"]` 두 리스트를 함께 수정한다. **두 extras 의 transformers 제약이 다르다**: llamafactory 는 `==5.5.4` 로 고정 (Qwen/LLaVA 검증값), unsloth 는 `>=5.5.4` 로 상한 해제 (학습 대상 google/gemma-4-E2B-it · google/gemma-4-E4B-it 가 최신 Gemma-4 loader 를 요구). 서브프로젝트(`LlamaFactory/`, `unsloth/`) 의 `pyproject.toml` 은 건드리지 않는다 — 충돌 시 README 의 `--upgrade` / `--no-deps` 회피 절차로 처리.
- Unsloth (Gemma-4 e2b/e4b) 학습은 `gui-model-unsloth` env 에서만 실행한다. `setup.py::EXTRAS["unsloth"]` 가 deepspeed 를 의존성에서 뺐기 때문에 이 env 에는 deepspeed 가 아예 존재하지 않는다 (FastModel 의 메모리 최적화 / gradient checkpointing 과 deepspeed ZeRO 충돌 방지, `accelerate launch` 의 deepspeed plugin 자동 활성화 방지). 예전 단일 env 시절의 `pip uninstall -y deepspeed` 토글 단계는 더 이상 필요 없다. LlamaFactory 백엔드 작업은 `gui-model-llamafactory` env 에서 수행하며 이쪽엔 `deepspeed>=0.10.0,<=0.18.4` 가 기본 포함된다.
- Gemma-4 e2b/e4b stage1 Full FT 의 Unsloth 권장 사양 키 (`load_in_16bit`, `optim`, `gradient_checkpointing`, `freeze_vision_tower`, `template`) 는 4 개 YAML (`unsloth/configs/GUI-Model-{MB,AC}/stage1_full/gemma-4-{e2b,e4b}_world-model.yaml`) 과 notebook `_MODEL_CONFIG` + Unsloth Stage1 YAML 생성 셀을 함께 동기화한다. `_unsloth_train.py` 가 `FastModel.from_pretrained(load_in_16bit=..., use_gradient_checkpointing=...)`, `get_chat_template(tokenizer, template)`, Full FT 분기에서 `freeze_vision_tower` 명시적 freeze, `SFTConfig.optim=...` 로 모두 소비하므로 키를 빼면 권장 동작이 깨진다. `gradient_checkpointing` 은 모델 로드 단계에서만 적용되며 `SFTConfig` 에 다시 넘기지 않는다.
- 문서나 스크립트에서 `outputs/{DS}/{category}/...` 의 `{DS}` 는 `MB` 또는 `AC`, `{category}` 는 `adapters | eval | merged`. `adapters/` 는 flat 네이밍 `{MODEL}_{detail}/` (Stage2 는 `{MODEL}_stage2_{MODE2}_{base|world_model_from_{MODE1}}/` 패턴). `merged/` 는 `{MODEL}_{detail}/epoch-{E}/` 로 epoch 별 서브디렉토리 분리 (예: `gemma-4-e2b_stage1_full/epoch-3/`, `qwen3-vl-8b_stage2_lora_world_model_from_full/epoch-2/`). `eval/` 은 `{MODEL}/stage{1,2}_eval/.../epoch-{E}/` 중첩 구조. `BEST_CHECKPOINT` 파일은 더 이상 생성되지 않는다.
- `data/` 아래 실제 디렉토리명은 `AndroidControl`, `MonkeyCollection`, `MobiBench` (평가 전용). MobiBench 는 `gui-model_stage{1,2}.jsonl` 두 단일 파일만 존재한다.
- eval script 에서 `vllm_infer.py` 호출 시 `--dataset_dir '$LF_ROOT/data'` (절대 경로) 를 반드시 전달한다. 상대 경로 사용 시 HF datasets 캐시 오염으로 이미지 `FileNotFoundError` 가 발생할 수 있다.
- **MobiBench dataset_info 자동 보장**: `_common.sh::ensure_eval_only_dataset_info()` 가 source 시점에 `dataset_info.json` 에 `GUI-Model-MB_stage{1,2}` 단일 파일 엔트리를 idempotent 하게 추가한다. notebook Section 1-2 를 돌리지 않은 환경에서도 `stage{1,2}_eval.sh --eval-datasets MB` 가 바로 동작하며, 두 경로(notebook + shell)는 동일한 JSON 을 쓴다.
- **JSONL `images` canonical prefix**: 모든 JSONL 의 `images` 필드는 `{DATASET_NAME}/images/...` 형태여야 한다 (AC/MB 공통). 누군가의 실수로 prefix 가 벗겨졌다면 [`scripts/fix_jsonl_image_paths.py`](./scripts/fix_jsonl_image_paths.py) 가 `images/...` → `{DATASET_NAME}/images/...` 로 복구한다 (idempotent, `--dry-run` 지원). 이 contract 는 `LF_ROOT/data/{DATASET_NAME}` symlink + `--dataset_dir $LF_ROOT/data` 조합과 맞물려 있어 prefix 가 없으면 `Image.open()` 이 cwd 기준으로 풀려 실패한다.
- Stage 1/2 merge 는 `outputs/{DS}/adapters/.../checkpoint-*` 가 하나라도 없으면 실패한다. 모든 epoch 을 순회해서 local merge + HF push 한다.
- Stage 2 train/merge (world-model variant) 는 `--stage1-epoch N` 으로 지정된 로컬 `outputs/{DS}/merged/{MODEL}_stage1_${STAGE1_MODE}/epoch-${N}/` 가 선행돼야 한다 (stage1_train → stage1_merge 로 생성). Stage 2 train 은 YAML 의 `model_name_or_path` 를 런타임에 sed 치환하므로 notebook YAML 생성 시 placeholder 값(HF id) 은 무시된다. Stage 2 eval 은 HF Hub merged repo 만 pull 하고 `--stage1-epoch` 값을 HF 레포명 계보 번호로 주입한다.
- HF repo id 조립은 `_common.sh::hf_repo_id_stage1` / `hf_repo_id_stage2_base` / `hf_repo_id_stage2_world_model` 세 헬퍼에 단일화되어 있다. 모든 merge/eval 스크립트가 이 헬퍼를 경유해야 네이밍 계약이 깨지지 않는다.
- [`scripts/stage1_train.sh`](./scripts/stage1_train.sh) 는 `FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=${NPROC_PER_NODE}` 를 붙여 실행하지만, [`scripts/stage2_train.sh`](./scripts/stage2_train.sh) 는 의도적으로 torchrun prefix 를 붙이지 않는다. `NPROC_PER_NODE` 는 `.env` 에서 관리하며 기본값은 2 이다. 두 노트북의 Section 0 YAML 생성 셀이 이 값으로 `gradient_accumulation_steps` 를 역계산 (`64 / (per_device * NPROC_PER_NODE)`) 해 global batch size 를 64 로 유지하므로, GPU 수를 바꾼 뒤에는 **변경을 적용할 노트북의** Section 0 CONFIGS 셀과 Stage 1/2 YAML 생성 셀을 다시 실행해야 한다 (LF / Unsloth 노트북이 서로 독립된 YAML 집합을 소유함). 나누어떨어지지 않는 조합은 `ValueError` 로 중단된다.
- [`scripts/split_data.py`](./scripts/split_data.py) 는 Stage 1 (random split, AC+MC) + Stage 2 (ID/OOD app-level split, AC only) 를 담당한다. MC 는 `_STAGE1_ONLY` 에 있어 Stage 2 는 자동 skip. MB 는 평가 전용이라 split 대상이 아니며 `DATASET_DIRS` 에도 없다. Stage 2 (AC) 는 `episodes_meta.jsonl.primary_app` 을 기준으로 app 집합을 in-domain/out-of-domain 으로 나누고 `gui-model_stage2_{train,test_id,test_ood}.jsonl` 을 생성한다. `primary_app` 값은 앱 라벨이 아닌 **package 식별자** (예: `com.ajnsnewmedia.kitchenstories`) 이며, AC 메타는 [`scripts/extract_androidcontrol_metadata.py`](./scripts/extract_androidcontrol_metadata.py) 가 각 step 의 `accessibility_trees` (AndroidAccessibilityForest proto) 에서 전경 application window 의 `package_name` 을 집계해 다수결로 뽑아 생성한다. 디코드에 `android-env` 패키지가 필요하다 (`pip install android-env`).
- bash 자동화는 bash 4+ 전제를 가진다.
- shell script CLI 공통 플래그:
  - **학습/merge (`stage{1,2}_{train,merge}.sh`)**: `--model MODEL --dataset {AC|MC|all} --stage1-mode {full|lora}`. `--dataset MB` 는 거절됨. `stage2_*`: `--stage2-mode {full|lora}` (기본 lora), `--stage1-epoch N` (world-model variant 전용).
  - **평가 (`stage{1,2}_eval.sh`)**: `--model MODEL --train-dataset {AC|MC} --eval-datasets LIST --stage1-mode ... --stage2-mode ... --stage1-epoch N --epochs LIST --variants LIST`. `--eval-datasets` 는 콤마 구분, 허용 `AC,MC,MB`, 기본값은 `--train-dataset` 단일값. Stage 2 eval 은 `--train-dataset AC` 만 (MC 는 Stage 2 데이터 없음).
    - Stage 1 variants: `base`, `full_world_model`, `lora_world_model`.
    - Stage 2 variants: `base`, `full_base`, `lora_base`, `full_world_model`, `lora_world_model`.

## 빠른 검증 포인트

- `pytest tests/test_action_eval.py -q` — 48 케이스 (parse/field_match/집계/ID-OOD aggregation/single-pair overall)
- `bash scripts/stage{1,2}_{train,merge,eval}.sh --help` — 모든 플래그 표기 확인
- `python scripts/split_data.py --dataset MonkeyCollection --help` (MC: Stage 2 자동 skip)
- `bash scripts/stage1_train.sh --dataset MB 2>&1` — 거절 메시지 ("MobiBench (MB) 는 평가 전용 벤치마크입니다") 확인
- HF naming 단위 검증:
  ```bash
  source scripts/_common.sh && parse_args
  hf_repo_id_stage1 qwen2.5-vl-7b AC full 3
  # → SaFD-00/qwen2.5-vl-7b-ac-world-model-stage1-full-epoch3
  hf_repo_id_stage1 qwen2.5-vl-7b MC full 3
  # → SaFD-00/qwen2.5-vl-7b-mc-world-model-stage1-full-epoch3
  hf_repo_id_stage2_world_model qwen2.5-vl-7b AC full 3 lora 1
  # → SaFD-00/qwen2.5-vl-7b-ac-world-model-stage1-full-epoch3-stage2-lora-epoch1
  ```
- `rg "BEST_CHECKPOINT" scripts/ tests/` — 비어야 함 (winner 개념 제거 후).

## 문서 동기화 원칙

- README 는 사용자 실행 순서 기준, ARCHITECTURE 는 실제 디렉토리/산출물 기준으로 유지한다.
- notebook section 순서가 바뀌면 README 와 ARCHITECTURE 의 section mapping 도 같이 갱신한다.
- shell script 전제조건이 바뀌면 README, ARCHITECTURE, AGENTS 를 함께 수정한다.
- 모델을 추가하면 notebook `_MODEL_CONFIG`, `_common.sh` 모델 레지스트리 + backend 매핑, README 모델 테이블, ARCHITECTURE 모델 레지스트리 테이블을 모두 갱신한다.
- `MODEL_BACKEND` ↔ notebook `_MODEL_CONFIG["backend"]` ↔ `unsloth/configs/` 디렉토리 일관성을 유지한다 (unsloth 모델만 해당).
