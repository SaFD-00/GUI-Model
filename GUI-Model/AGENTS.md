# AGENTS.md

`GUI-Model/` 하위 프로젝트에서 작업하는 에이전트를 위한 가이드다.

## 현재 코드 기준 요약

- 이 프로젝트의 실제 실행 엔트리포인트는 [`gui-model.ipynb`](./gui-model.ipynb) 와 [`scripts/`](./scripts) 다.
- 12개 Vision-Language 모델(Qwen2-VL, Qwen2.5-VL, Qwen3-VL, Gemma-4, LLaVA 계열)을 지원한다.
- 모델 레지스트리는 두 곳에 있다: notebook Cell 3 의 `_MODEL_CONFIG` (backend 필드 포함) 와 `scripts/_common.sh` 의 `MODEL_ID`/`MODEL_TEMPLATE`/`MODEL_BACKEND`.
- 학습/export 는 모델별 backend 에 따라 분기된다:
  - `backend=llamafactory` (기본, Qwen/LLaVA 계열): 저장소 내부 [`LlamaFactory/`](./LlamaFactory) clone + `llamafactory-cli`.
  - `backend=unsloth` (Gemma-4 계열): 저장소 내부 [`unsloth/`](./unsloth) clone + `scripts/_unsloth_{train,merge}.py`.
- 평가 (`scripts/stage{1,2}_eval.sh`) 는 backend 독립이다.
- [`gui_model/`](./gui_model) 패키지는 사실상 배포용 스텁이며, 핵심 파이프라인 로직은 여기에 없다.
- 데이터셋 장기 이름은 `MobiBench`, `AndroidControl` 이고, shell script 내부 단축 코드는 `MB`, `AC` 다.

## 어디를 수정해야 하는가

- **모델 추가**: `gui-model.ipynb` Cell 6 의 `_MODEL_CONFIG` (backend 필드 포함) + `scripts/_common.sh` 의 `MODEL_ID`, `MODEL_TEMPLATE`, `ALL_MODELS` 를 동시에 수정한다. backend 가 기본값(`llamafactory`) 이 아니면 `_common.sh` 의 `MODEL_BACKEND` 매핑에도 등록한다. 모든 backend 는 Stage 1 YAML 을 **full / lora 두 벌** 자동 생성한다 (Cell 9 LF / Cell 11 Unsloth). LoRA 모드 per-model 추가 조정은 `hparam_overrides["stage1_lora"]` 를 쓴다. notebook 실행 셀(Stage 1/2 train, merge) 은 default=full 기준이고, LoRA 학습은 쉘 스크립트의 `--stage1-mode lora` 로 돌린다.
- **Backend 변경**: 기존 모델을 다른 backend 로 옮길 때는 `_common.sh::MODEL_BACKEND` 한 줄만 수정하면 된다. 단, 해당 backend 용 YAML 이 준비되어 있어야 한다.
- **notebook 실행 순서나 YAML 생성 흐름**: [`gui-model.ipynb`](./gui-model.ipynb) 와 [`scripts/stage1_*.sh`](./scripts/stage1_train.sh), [`scripts/stage2_*.sh`](./scripts/stage2_train.sh) 를 함께 맞춰라.
- **데이터 분할 규칙**: [`scripts/split_data.py`](./scripts/split_data.py) 가 기준이다.
- **Stage 1 평가**: [`scripts/_hungarian_eval.py`](./scripts/_hungarian_eval.py) 가 기준. 흐름은 **train → merge → eval** — `stage1_merge.sh` 가 모든 epoch 를 각각 local merge + HF push 한 뒤 (merge 는 `checkpoint-*/trainer_state.json` 에서 epoch 을 파싱; `_common.sh::ckpt_epoch_from_dir`), `stage1_eval.sh` 가 baseline + `--epochs` 리스트 (기본 `1,2,3`) 에 해당하는 HF Hub merged repo (`SaFD-00/...stage1-${MODE}-world-model-epoch{E}`) sweep 을 돌고 `avg_hungarian_f1` winner 를 adapter 경로의 `BEST_CHECKPOINT`(plain `epoch-{E}`) + `BEST_CHECKPOINT.json`(`epoch`, `hf_repo_id` 필드 포함) 에 기록한다. eval 단계는 로컬 `checkpoint-*` 를 조회하지 않으므로 학습 머신이 아닌 환경에서도 재평가 가능. 재실행 시 marker (`hungarian_metrics.json` / `BEST_CHECKPOINT.json`) 존재하는 unit 은 자동 skip 되므로 부분 실패 후 이어 돌리기가 안전하다. 경량 분할 스크립트 [`scripts/stage1_eval_base.sh`](./scripts/stage1_eval_base.sh) (Phase A) / [`scripts/stage1_eval_world_model.sh`](./scripts/stage1_eval_world_model.sh) (Phase B+C) 도 동일 규칙. 정본은 notebook 의 Stage 1 eval 셀 (Section 5, 상단 `EPOCHS` 리스트로 조정).
- **Stage 2 평가**: [`scripts/_action_eval.py`](./scripts/_action_eval.py) 가 기준 (winner metric `step_accuracy`). 흐름은 `stage2_train.sh` → `stage2_merge.sh` (variant × 전 epoch push) → `stage2_eval.sh --epochs 1,2,3` (variant × 지정 epoch HF Hub sweep, 기본 `1,2,3`). 로컬 `merged/{MODEL}_stage1_${MODE}/` 와 `adapters/.../checkpoint-*` 어느 쪽에도 의존하지 않고 HF Hub `SaFD-00/...stage2-{base|${MODE}-world-model}-epoch{E}` 만 pull. Stage 2 world-model **train/merge** 는 여전히 로컬 S1 winner merged (`epoch-{Ewin}/`) 를 base 로 사용 (`BEST_CHECKPOINT.json.epoch` 자동 해석). 재실행 시 marker (`action_metrics.json` / `BEST_CHECKPOINT.json`) 존재하는 unit 은 variant 별로 독립 skip 된다. 경량 분할 스크립트 [`scripts/stage2_eval_base.sh`](./scripts/stage2_eval_base.sh) (Phase A) / [`scripts/stage2_eval_world_model.sh`](./scripts/stage2_eval_world_model.sh) (Phase B+C, 레거시 `lora_world_model` variant 고정) 도 동일 규칙. 정본은 notebook Section 8, 회귀 테스트 [`tests/test_action_eval.py`](./tests/test_action_eval.py). 메트릭 정의는 [`ARCHITECTURE.md`](./ARCHITECTURE.md) §6 참고.
- **shell 실행 공통 규약**: `MB`/`AC` 매핑, 모델 레지스트리 → [`scripts/_common.sh`](./scripts/_common.sh)
- **Python 의존성**: [`setup.py`](./setup.py) 가 실제 설치 기준.

## 작업 시 주의점

- `LlamaFactory/`, `unsloth/` 내부 파일은 마지막 수단으로만 수정하라. 가능하면 notebook, local shell script, custom YAML (`LlamaFactory/examples/custom/...` 또는 `unsloth/configs/...`), 평가 helper 로 해결한다.
- trl / transformers / peft 의 API 변경(예: trl 0.24 의 `max_length`·`processing_class`, transformers 5.x 의 `overwrite_output_dir` 제거)에 대응할 때는 `scripts/_unsloth_train.py` 내부에서만 호환 계층을 유지하고, `unsloth/` 하위는 건드리지 않는다.
- transformers 버전을 바꿀 때는 [`pyproject.toml`](./pyproject.toml) `[project].dependencies` 와 [`setup.py`](./setup.py) `INSTALL_REQUIRES` 두 곳을 함께 수정한다 (현재 `transformers==5.5.4` 로 고정). 서브프로젝트(`LlamaFactory/`, `unsloth/`) 의 `pyproject.toml` 은 건드리지 않는다 — 충돌 시 README 의 `--upgrade` / `--no-deps` 회피 절차로 처리.
- Unsloth (Gemma-4 e2b/e4b) 학습 셀을 실행하기 전 `gui-model.ipynb` Cell 5 (`%%bash pip uninstall -y deepspeed`) 로 deepspeed 를 env 에서 반드시 제거한다. LlamaFactory 백엔드 학습으로 돌아갈 때는 `pip install -e .` 로 `deepspeed>=0.10.0,<=0.18.4` 를 재설치한다.
- Gemma-4 e2b/e4b stage1 Full FT 의 Unsloth 권장 사양 키 (`load_in_16bit`, `optim`, `gradient_checkpointing`, `freeze_vision_tower`, `template`) 는 4 개 YAML (`unsloth/configs/GUI-Model-{MB,AC}/stage1_full/gemma-4-{e2b,e4b}_world-model.yaml`) 과 notebook `_MODEL_CONFIG` + Unsloth Stage1 YAML 생성 셀을 함께 동기화한다. `_unsloth_train.py` 가 `FastModel.from_pretrained(load_in_16bit=..., use_gradient_checkpointing=...)`, `get_chat_template(tokenizer, template)`, Full FT 분기에서 `freeze_vision_tower` 명시적 freeze, `SFTConfig.optim=...` 로 모두 소비하므로 키를 빼면 권장 동작이 깨진다. `gradient_checkpointing` 은 모델 로드 단계에서만 적용되며 `SFTConfig` 에 다시 넘기지 않는다.
- 문서나 스크립트에서 `outputs/{DS}/{category}/...` 의 `{DS}` 는 `MB` 또는 `AC`, `{category}` 는 `adapters | eval | merged`. `adapters/` 는 flat 네이밍 `{MODEL}_{detail}/` + `BEST_CHECKPOINT[.json]`. `merged/` 는 `{MODEL}_{detail}/epoch-{E}/` 로 epoch 별 서브디렉토리 분리 (예: `gemma-4-e2b_stage1_full/epoch-3/`, `qwen3-vl-8b_stage2_lora_world_model_from_full/epoch-2/`). `eval/` 은 `{MODEL}/stage{1,2}_eval/.../epoch-{E}/` 중첩 구조. 모든 산출물의 루트는 `GUI-Model/outputs/` 이다.
- `data/` 아래 실제 디렉토리명은 `MobiBench`, `AndroidControl` 이다.
- eval script 에서 `vllm_infer.py` 호출 시 `--dataset_dir '$LF_ROOT/data'` (절대 경로) 를 반드시 전달한다. 상대 경로 사용 시 HF datasets 캐시 오염으로 이미지 `FileNotFoundError` 가 발생할 수 있다.
- Stage 1/2 merge 는 `outputs/{DS}/adapters/.../checkpoint-*` 가 하나라도 없으면 실패한다. `BEST_CHECKPOINT` 입력을 요구하지 않으며 모든 epoch 을 push 한다.
- Stage 2 train/merge 는 로컬 `outputs/{DS}/merged/{MODEL}_stage1_${MODE}/epoch-{Ewin}/` 와 `adapters/{MODEL}_stage1_${MODE}/BEST_CHECKPOINT.json` 이 필수 선행이다 (`stage1_eval.sh` 가 기록). Stage 2 train 은 YAML 의 `model_name_or_path` 를 런타임에 sed 치환하므로 notebook YAML 생성 시 placeholder 값(원본 HF id) 은 무시된다. Stage 2 eval 은 로컬 S1 merged 참조가 제거되어 HF Hub 만 사용한다. Stage 2 스크립트의 `--stage1-mode` 가 상류 소스를 결정한다.
- HF repo id 조립은 `_common.sh::hf_repo_id_stage{1,2}` 에 단일화되어 있다. 모든 merge/eval 스크립트와 기타 도구가 이 헬퍼를 경유해야 네이밍 계약이 깨지지 않는다.
- [`scripts/stage1_train.sh`](./scripts/stage1_train.sh) 는 `FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=${NPROC_PER_NODE}` 를 붙여 실행하지만, [`scripts/stage2_train.sh`](./scripts/stage2_train.sh) 는 의도적으로 torchrun prefix 를 붙이지 않는다. `NPROC_PER_NODE` 는 `.env` 에서 관리하며 기본값은 2 이다. notebook 의 YAML 생성 셀이 이 값으로 `gradient_accumulation_steps` 를 역계산 (`64 / (per_device * NPROC_PER_NODE)`) 해 global batch size 를 64 로 유지하므로, GPU 수를 바꾼 뒤에는 Section 0 의 Cell 6 과 YAML 생성 셀(9/11/15/17/61) 을 다시 실행해야 한다. 나누어떨어지지 않는 조합은 `ValueError` 로 중단된다.
- [`scripts/split_data.py`](./scripts/split_data.py) 는 `AndroidControl` Stage 2 에 대해 기본적으로 `30000`개 stratified subsample 을 만든 뒤 train/test split 한다.
- bash 자동화는 bash 4+ 전제를 가진다.
- shell script CLI 는 `--model MODEL --dataset DS --stage1-mode {full|lora}` 플래그 방식이다 (기본: full). `stage{1,2}_eval.sh` 는 추가로 `--epochs LIST` (콤마 구분, 기본 `1,2,3`) 를 받아 HF Hub sweep 대상 epoch 를 지정한다.

## 빠른 검증 포인트

- `bash scripts/stage1_train.sh --help`
- `bash scripts/stage2_eval.sh --help`
- `python scripts/split_data.py --dataset MobiBench --help`
- `rg "BEST_CHECKPOINT|GUI-Model-MB|GUI-Model-AC" gui-model.ipynb scripts`

## 문서 동기화 원칙

- README 는 사용자 실행 순서 기준, ARCHITECTURE 는 실제 디렉토리/산출물 기준으로 유지한다.
- notebook section 순서가 바뀌면 README 와 ARCHITECTURE 의 section mapping 도 같이 갱신한다.
- shell script 전제조건이 바뀌면 README, ARCHITECTURE, AGENTS 를 함께 수정한다.
- 모델을 추가하면 notebook `_MODEL_CONFIG`, `_common.sh` 모델 레지스트리 + backend 매핑, README 모델 테이블, ARCHITECTURE 모델 레지스트리 테이블을 모두 갱신한다.
- `MODEL_BACKEND` ↔ notebook `_MODEL_CONFIG["backend"]` ↔ `unsloth/configs/` 디렉토리 일관성을 유지한다 (unsloth 모델만 해당).
