# AGENTS.md

`GUI-Model/` 하위 프로젝트에서 작업하는 에이전트를 위한 가이드다.

## 현재 코드 기준 요약

- 이 프로젝트의 실제 실행 엔트리포인트는 [`gui-model.ipynb`](./gui-model.ipynb) 와 [`scripts/`](./scripts) 다.
- 12개 Vision-Language 모델(Qwen2-VL, Qwen2.5-VL, Qwen3-VL, Gemma-4, LLaVA 계열)을 지원한다.
- 모델 레지스트리는 두 곳에 있다: notebook Cell 3 의 `_MODEL_CONFIG` 와 `scripts/_common.sh` 의 `MODEL_ID`/`MODEL_TEMPLATE`.
- 학습과 export 엔진은 저장소 내부의 `LlamaFactory/` clone 에 의존한다.
- [`gui_model/`](./gui_model) 패키지는 사실상 배포용 스텁이며, 핵심 파이프라인 로직은 여기에 없다.
- 데이터셋 장기 이름은 `MobiBench`, `AndroidControl` 이고, shell script 내부 단축 코드는 `MB`, `AC` 다.

## 어디를 수정해야 하는가

- **모델 추가**: `gui-model.ipynb` Cell 3 의 `_MODEL_CONFIG` + `scripts/_common.sh` 의 `MODEL_ID`, `MODEL_TEMPLATE`, `ALL_MODELS` 를 동시에 수정한다. notebook 실행 셀(Stage 1/2 train, merge)에도 새 모델 블록을 추가해야 한다.
- **notebook 실행 순서나 YAML 생성 흐름**: [`gui-model.ipynb`](./gui-model.ipynb) 와 [`scripts/stage1_*.sh`](./scripts/stage1_train.sh), [`scripts/stage2_*.sh`](./scripts/stage2_train.sh) 를 함께 맞춰라.
- **데이터 분할 규칙**: [`scripts/split_data.py`](./scripts/split_data.py) 가 기준이다.
- **Stage 1 평가**: [`scripts/_hungarian_eval.py`](./scripts/_hungarian_eval.py), **Stage 2 평가**: [`scripts/_action_eval.py`](./scripts/_action_eval.py) 가 기준이다.
- **shell 실행 공통 규약**: `MB`/`AC` 매핑, 모델 레지스트리 → [`scripts/_common.sh`](./scripts/_common.sh)
- **Python 의존성**: [`setup.py`](./setup.py) 가 실제 설치 기준.

## 작업 시 주의점

- `LlamaFactory/` 내부 파일은 마지막 수단으로만 수정하라. 가능하면 notebook, local shell script, custom YAML, 평가 helper 로 해결한다.
- 문서나 스크립트에서 `saves/{MODEL}/{DS}/...` 의 `{MODEL}` 은 모델 short_name (예: `qwen3-vl-8b`), `{DS}` 는 `MB` 또는 `AC` 이다.
- `data/` 아래 실제 디렉토리명은 `MobiBench`, `AndroidControl` 이다.
- Stage 1 merge 는 `saves/{MODEL}/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT` 가 없으면 실패한다.
- Stage 2 eval 과 merge 는 로컬 `outputs/{MODEL}/{DS}/stage1_merged/` 를 전제로 한다.
- [`scripts/stage1_train.sh`](./scripts/stage1_train.sh) 는 `FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=4` 를 붙여 실행하지만, [`scripts/stage2_train.sh`](./scripts/stage2_train.sh) 는 의도적으로 torchrun prefix 를 붙이지 않는다.
- [`scripts/split_data.py`](./scripts/split_data.py) 는 `AndroidControl` Stage 2 에 대해 기본적으로 `30000`개 stratified subsample 을 만든 뒤 train/test split 한다.
- bash 자동화는 bash 4+ 전제를 가진다.
- shell script CLI 는 `--model MODEL --dataset DS` 플래그 방식이다.

## 빠른 검증 포인트

- `bash scripts/stage1_train.sh --help`
- `bash scripts/stage2_eval.sh --help`
- `python scripts/split_data.py --dataset MobiBench --help`
- `rg "BEST_CHECKPOINT|GUI-Model-MB|GUI-Model-AC" gui-model.ipynb scripts`

## 문서 동기화 원칙

- README 는 사용자 실행 순서 기준, ARCHITECTURE 는 실제 디렉토리/산출물 기준으로 유지한다.
- notebook section 순서가 바뀌면 README 와 ARCHITECTURE 의 section mapping 도 같이 갱신한다.
- shell script 전제조건이 바뀌면 README, ARCHITECTURE, AGENTS 를 함께 수정한다.
- 모델을 추가하면 notebook `_MODEL_CONFIG`, `_common.sh` 모델 레지스트리, README 모델 테이블, ARCHITECTURE 모델 레지스트리 테이블을 모두 갱신한다.
