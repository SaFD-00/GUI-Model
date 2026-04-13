# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GUI-Model은 모바일 GUI **World Modeling이 Action Prediction 성능에 미치는 영향**을 정량 검증하는 Ablation Study(3-Way Comparison) 프로젝트이다. Qwen3-VL-8B-Instruct를 Base Model로, LLaMA-Factory 프레임워크에서 2-Stage fine-tuning 파이프라인을 실행한다.

- **Stage 1 (World Modeling)**: UI 상태(XML) + 액션 + 스크린샷 → 다음 UI 상태(XML) 예측 (Full FT)
- **Stage 2 (Action Prediction)**: 스크린샷 + UI 상태 + 태스크 → 액션(JSON) 예측 (LoRA FT)

**핵심 가설**: World Modeling 사전학습이 downstream Action Prediction 성능을 향상시킨다.

이 저장소는 상위 Monkey-Collector 프로젝트(GUI Foundation Model 구축)의 **실험 검증 파트**에 해당한다. 학습 및 평가는 완료된 상태이다.

## 3-Way Experiment Design

| Exp | Stage 1 | Stage 2 | Base Model | 목적 |
|-----|---------|---------|------------|------|
| Exp-1 | Full FT | — | Qwen3-VL-8B-Instruct | World Model 품질 평가 |
| stage1+stage2 | Full FT → Merge | LoRA FT | SaFD-00/qwen3-vl-8b-stage1-world-model | 핵심: World Model → Action |
| stage2 | — | LoRA FT | Qwen3-VL-8B-Instruct | Control Group (Baseline) |

**Baseline**: Qwen3-VL-8B-Instruct Zero-shot (학습 없음)

## 데이터셋 설정

Cell 3에서 `CONFIGS` 딕셔너리로 두 데이터셋(MobiBench/AndroidControl) 모두 자동 설정된다. 별도 전환 불필요.

| 설정 | MobiBench | AndroidControl |
|------|-----------|----------------|
| LF subfolder | `GUI-Model-MB` | `GUI-Model-AC` |
| DS prefix | `GUI-Model-MB` | `GUI-Model-AC` |
| Output prefix | `MB/` | `AC/` |
| HF slug | `mb-` | `ac-` |
| Stage 1 epochs / LR | 5 / 1e-5 | 3 / 1e-5 |
| Stage 2 epochs / LR | 5 / 3e-5 | 3 / 5e-5 |
| Stage 2 LoRA r / α | 16 / 32 | 32 / 64 |
| save / eval strategy | epoch | epoch |
| per_device_eval_batch_size | 1 | 4 |

## Commands

두 가지 실행 경로. **YAML 생성은 notebook 전용**이므로, 최초 실행은 notebook, 반복 실행은 shell script 가 표준이다.

### A. Shell Scripts (권장, 재실행·자동화)

`gui-model.ipynb` Cell 3/7/10/14/17/20/30/34 를 먼저 실행해 YAML·dataset_info 등록 파일이 존재한다는 전제하에, 학습/평가/Merge 단계는 `scripts/` 쉘 스크립트로 반복 실행한다.

```bash
# 데이터 Split (학습 전 1회 실행)
python scripts/split_data.py --dataset MobiBench
python scripts/split_data.py --dataset AndroidControl

# 공통 인자: MB | AC | all (기본 all). 로그는 logs/<tag>_<ts>.log 로 tee 저장.

# Stage 1  (Hungarian F1 기반 Best Epoch 자동 선택)
./scripts/stage1_train.sh        # Full FT + checkpoint-* 생성 + load_best_model_at_end safety
./scripts/stage1_eval.sh         # Baseline + 전체 checkpoint sweep → Hungarian F1 winner 선택
                                 # → outputs/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT 기록
./scripts/stage1_merge.sh        # BEST_CHECKPOINT 기반 merge → HF Hub push + outputs/{DS}/stage1_merged/ 로컬 복사

# Stage 2  (Overall Score 기반 Best Epoch 자동 선택, lora_base/lora_world_model 각각)
./scripts/stage2_train.sh        # LoRA base / world_model + checkpoint-* 생성
./scripts/stage2_eval.sh         # Baseline + 각 variant 별 체크포인트 sweep → Overall Score winner 선택
                                 # → outputs/{DS}/stage2_lora/{lora_base,lora_world_model}/BEST_CHECKPOINT 기록
                                 # (로컬 경로 + --adapter_name_or_path 사용, HF 의존 X)
./scripts/stage2_merge.sh        # 각 variant BEST_CHECKPOINT 읽어 winner adapter merge → HF Hub push
                                 # (world_model variant 의 base 는 로컬 outputs/{DS}/stage1_merged)

# 단일 데이터셋
./scripts/stage1_train.sh MB     # MobiBench 만
./scripts/stage1_train.sh AC     # AndroidControl 만
```

스크립트 전제: bash 4+ (macOS 기본 3.2 → `brew install bash`), LLaMA-Factory 설치 완료, `.env` 에 `HF_TOKEN` (merge 스크립트만 요구). `set -euo pipefail` 로 단계 실패 시 즉시 중단.

### B. 수동 llamafactory-cli (디버깅·단일 셀 재현)

```bash
# Stage 1: World Modeling Full FT (H100 80GB × 4)
cd LlamaFactory
FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=4 \
  llamafactory-cli train examples/custom/GUI-Model-MB/stage1_full/qwen3_vl_8b_gui.yaml

# Stage 1 평가: eval_loss
llamafactory-cli train examples/custom/GUI-Model-MB/stage1_eval/base/eval_loss.yaml

# Stage 1 평가: predict (Hungarian Matching)  — vllm_infer.py 사용
python scripts/vllm_infer.py --model_name_or_path <model> --dataset <ds_test> ...

# Stage 2: Action Prediction LoRA FT (노트북 Cell 31/32 에는 torchrun prefix 없음)
llamafactory-cli train examples/custom/GUI-Model-MB/stage2_lora/<base|world_model>.yaml
```

## Architecture

### 실행 파이프라인

```
[gui-model.ipynb]  ──  전체 파이프라인 오케스트레이션 (Jupyter 노트북)
       │
       ├── Section 0: 환경 설정, LLaMA-Factory 설치
       ├── Section 1-2: 데이터 등록 (dataset_info.json에 추가)
       ├── Section 3: Stage 1 학습 (Full FT, DeepSpeed ZeRO-3)
       ├── Section 4: Stage 1 모델 Merge & HuggingFace 업로드
       ├── Section 5: Stage 1 평가 (eval_loss + predict + Hungarian Matching)
       ├── Section 6: Stage 2 학습 (LoRA FT × 3 실험)
       ├── Section 7: Stage 2 모델 Merge & HuggingFace 업로드
       └── Section 8: Stage 2 평가 (3-Way + Baseline 비교)
```

### 핵심 컴포넌트

- **gui-model.ipynb**: 전체 파이프라인의 유일한 실행 엔트리포인트. 데이터 준비, 학습, 평가, 모델 배포를 순차 실행
- **LlamaFactory/**: LLaMA-Factory 프레임워크 (submodule 또는 클론). 학습/평가 엔진
- **LlamaFactory/examples/custom/GUI-Model-MB/**: 학습/평가 YAML 설정 파일
  - `stage1_full/`: Stage 1 Full FT 설정
  - `stage1_eval/`: Stage 1 평가 (eval_loss.yaml, predict.yaml)
  - `stage2_lora/`: Stage 2 LoRA 설정 (stage2, stage1+stage2)
  - `stage2_eval/`: Stage 2 평가 설정
- **scripts/**: 유틸리티 + 실행 스크립트
  - `split_data.py`: Train/Test Split CLI (Stage 1 random, Stage 2 stratified)
  - `extract_androidcontrol_images.py`: AndroidControl 이미지 추출
  - `_common.sh`: 쉘 스크립트 공통 헬퍼 (`BASE_DIR`/`LF_ROOT` 자동 감지, `DS_PREFIX`/`HF_SLUG` 매핑, `parse_dataset_arg`, `run_logged` tee 로거, `require_yaml` 가드)
  - `_hungarian_eval.py`: Stage 1 체크포인트별 Hungarian/BLEU/ROUGE 메트릭 + winner 선택. `score` (단일 prediction → metrics.json), `select` (checkpoint-*/metrics.json 비교 → `BEST_CHECKPOINT` 기록) 서브커맨드. notebook Cell 25+26 포팅으로 shell/notebook 결과 동일 보장
  - `_action_eval.py`: Stage 2 체크포인트별 Action 메트릭 (Parse/Type/IoU/Params/Overall) + winner 선택. `score`/`select` 서브커맨드. notebook Cell 41+42 포팅. 기본 선택 지표는 `overall_score` (Type × (0.5×IoU + 0.5×Params))
  - `stage1_train.sh` / `stage1_eval.sh` / `stage1_merge.sh`: Stage 1 파이프라인. `stage1_eval.sh` 는 Baseline + 체크포인트 sweep + Hungarian F1 winner 선택, `stage1_merge.sh` 는 BEST_CHECKPOINT 기반 merge (HF + local)
  - `stage2_train.sh` / `stage2_eval.sh` / `stage2_merge.sh`: Stage 2 파이프라인. `stage2_eval.sh` 는 Baseline + `lora_base`/`lora_world_model` 각각 체크포인트 sweep + Overall Score winner 선택 (로컬 base + `--adapter_name_or_path`, HF 의존 없음). `stage2_merge.sh` 는 각 variant BEST_CHECKPOINT 읽어 winner adapter 로 merge. Stage 2 train 은 노트북 원본에 맞춰 `FORCE_TORCHRUN` prefix 미사용
- **LlamaFactory/outputs/**: 학습 체크포인트 및 평가 결과
  - `stage1_eval/eval_loss/`: Stage 1 loss 메트릭
  - `stage1_eval/hungarian_matching/`: Stage 1 요소 수준 메트릭
  - `stage2_eval/{base,lora_base,lora_world_model}/`: 3-Way 평가 리포트
- **.claude/reference/metrics/**: 커스텀 평가 메트릭 구현
  - `metric.py`: LLaMA-Factory SFT eval에 BLEU, ROUGE, Hungarian 통합한 커스텀 메트릭
  - `hungarian_metric.py`: BeautifulSoup + Munkres 알고리즘 기반 XML 요소 1:1 매칭 정확도

## Data

### 데이터셋 위치

```
data/
├── MobiBench/
│   ├── images/                      # 모바일 UI 스크린샷 (3,655개 PNG, MobiBench/images/episode_{id:06d}_step_{idx:04d}.png)
│   ├── gui-model_stage1.jsonl       # Stage 1 전체 (3,145건 = train 2,987 + test 158)
│   └── gui-model_stage2.jsonl       # Stage 2 전체 (3,147건 = train 2,987 + test 160)
└── AndroidControl/
    ├── images/                      # AC 스크린샷 (extract_androidcontrol_images.py 로 추출, AndroidControl/images/episode_{id:06d}_step_{idx:04d}.png)
    ├── gui-model_stage1.jsonl       # Stage 1 전체 (71,047건 = train 67,494 + test 3,553)
    └── gui-model_stage2.jsonl       # Stage 2 전체 (91,677건 = train 87,090 + test 4,587)

data/
├── MobiBench/
│   ├── gui-model_stage1.jsonl           # 원본
│   ├── gui-model_stage1_train.jsonl     # split_data.py로 생성
│   ├── gui-model_stage1_test.jsonl
│   ├── gui-model_stage2.jsonl
│   ├── gui-model_stage2_train.jsonl
│   ├── gui-model_stage2_test.jsonl
│   └── images/
└── AndroidControl/
    ├── (동일 구조)
    └── images/

LlamaFactory/data/dataset_info.json     # 상대 경로로 원본 참조 (../../data/{DATASET_NAME}/...)
                                         # JSONL 파일은 복사하지 않음, 이미지만 symlink
```

### 데이터 포맷

**ShareGPT Multimodal Format** (LLaMA-Factory 호환):

```json
{
  "messages": [
    {"from": "system", "value": "System prompt"},
    {"from": "human", "value": "<image>\n[UI XML]\n[Action/Task]"},
    {"from": "gpt", "value": "[Target XML 또는 Action JSON]"}
  ],
  "images": ["path/to/screenshot.png"]
}
```

**Action JSON Format** (Stage 2 출력):

```json
{
  "type": "click | input | swipe | long_click | openapp",
  "params": {},
  "default": true,
  "index": 23,
  "bounds": {"left": 100, "top": 200, "width": 50, "height": 50}
}
```

## Evaluation Metrics

### Stage 1 (World Modeling)

- **Loss**: eval_loss, Perplexity
- **Text Generation**: BLEU-4, ROUGE-L, Exact Match
- **Hungarian Matching**: XML에서 interactive 요소 추출 → Munkres 최적 1:1 매칭 → EA, F1, Precision, Recall, Text Similarity, Index Accuracy

### Stage 2 (Action Prediction)

- **Parse Rate**: JSON 파싱 성공률
- **Type Accuracy**: 액션 타입 일치율
- **Bounds IoU**: Bounding box 겹침 비율 (현재 전 모델 0.0 — 좌표 형식 차이 이슈, 추후 조사 필요)
- **Params Accuracy**: 액션 파라미터 일치율
- **Overall Score**: Type × (0.5×IoU + 0.5×Params)

## Key Design Decisions

- **LLaMA-Factory 의존**: 학습/평가 모두 LLaMA-Factory CLI로 실행. 커스텀 학습 코드 없음
- **Template**: `qwen3_vl_nothink` — Qwen3-VL의 thinking 모드를 비활성화한 템플릿
- **Vision Tower Frozen**: 모든 실험에서 vision encoder는 동결. LLM backbone만 학습
- **Stage 1 Full FT → Stage 2 LoRA**: Stage 1에서 전체 파라미터를 GUI 도메인에 적응 후, Stage 2에서 LoRA로 효율적 태스크 학습
- **DeepSpeed**: Stage 1은 ZeRO-3 (H100 × 4), Stage 2는 ZeRO-2 (H100 × 4)
- **cutoff_len: 8192**: XML이 포함된 긴 입력을 처리하기 위한 설정
- **커스텀 메트릭 패치**: LLaMA-Factory의 `metric.py`에 Hungarian Matching을 통합하여 eval 시 자동 산출. 패치 가이드는 `.claude/reference/metrics/patch_guide_0315.txt` 참조
- **Best Epoch 자동 선택 (Stage 1: Hungarian F1, Stage 2: Overall Score)**: `load_best_model_at_end=true` 는 eval_loss 기준(intrinsic)으로 자동 선택하여 safety net 역할. 실제 winner 는 extrinsic generation quality 기준으로 선택:
  - **Stage 1**: `stage1_eval.sh` 가 각 `checkpoint-*` 를 vllm_infer 로 생성평가 + `_hungarian_eval.py` 로 Hungarian F1 계산. 결과는 `outputs/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT`. `stage1_merge.sh`/Cell 17 이 이를 읽어 HF Hub + `outputs/{DS}/stage1_merged/` 양쪽 merge
  - **Stage 2**: `stage2_eval.sh` 가 `lora_base` / `lora_world_model` 각각 체크포인트 sweep (Base: Qwen or `outputs/{DS}/stage1_merged`, Adapter: `checkpoint-*/`) → `_action_eval.py` 로 `overall_score` 계산 → 각 variant 의 `outputs/{DS}/stage2_lora/{lora_base,lora_world_model}/BEST_CHECKPOINT` 기록. `stage2_merge.sh`/Cell 34 가 각각 읽어 winner adapter 로 merge → HF Hub push (변수 수만큼 2개 모델 push). lora_base 와 lora_world_model 둘 다 동일 로직으로 3-Way 비교 공정성 유지
  - Shell 파이프라인은 로컬 경로만 사용해 HF 의존을 제거 (stage2_merge 이전에도 stage2_eval 실행 가능, 404/네트워크 이슈 없음)

## Configuration

- **데이터 Split**: `python scripts/split_data.py --dataset {DATASET_NAME}` (학습 전 1회 실행)
- **데이터셋 설정**: `gui-model.ipynb` Cell 3에서 `CONFIGS` 딕셔너리로 양 데이터셋 자동 설정
- **데이터 등록**: Cell 8/11 실행 시 `dataset_info.json`에 상대 경로(`../../data/{DATASET_NAME}/...`)로 자동 등록. 파일 복사 없음
- **Stage 1 학습**: `LlamaFactory/examples/custom/GUI-Model-MB/stage1_full/qwen3_vl_8b_gui.yaml` (노트북에서 동적 생성)
- **Stage 2 학습**: `gui-model.ipynb` Section 6에서 직접 설정 (LoRA config는 노트북 내 정의)
- **환경변수**: `.env.example` 참조 (HuggingFace token 등)

## Key Hyperparameters

### MobiBench (기본)

| 항목 | Stage 1 (Full FT) | Stage 2 (LoRA) |
|------|-------------------|----------------|
| batch × accum × GPU | 2 × 8 × 4 = 64 | 2 × 4 × 4 = 32 |
| learning_rate | 1e-5 | 3e-5 |
| epochs | 5 | 5 |
| lr_scheduler | cosine | cosine |
| warmup_ratio | 0.05 | 0.05 |
| weight_decay | 0.01 | 0.01 |
| max_grad_norm | 1.0 | 1.0 |
| save_strategy / save_total_limit | epoch / 5 | epoch / 5 |
| eval_strategy | epoch | epoch |
| load_best_model_at_end | true (eval_loss ↓) | true (eval_loss ↓) |
| LoRA r / α / dropout | — | 16 / 32 / 0.1 |
| DeepSpeed | ZeRO-3 | — |
| image_max_pixels | 4,233,600 | 4,233,600 |

### AndroidControl (MobiBench 대비 Stage 1 ~23배 / Stage 2 ~29배 대규모)

| 항목 | Stage 1 (Full FT) | Stage 2 (LoRA) |
|------|-------------------|----------------|
| batch × accum × GPU | 2 × 8 × 4 = 64 | 2 × 8 × 4 = 64 |
| learning_rate | 1e-5 | 5e-5 |
| epochs | 3 | 3 |
| warmup_ratio | 0.03 | 0.03 |
| save_strategy / save_steps | epoch / — | epoch / — |
| eval_strategy / eval_steps | epoch / — | epoch / — |
| save_total_limit | 5 | 5 |
| per_device_eval_batch_size | 4 | 4 |
| weight_decay | 0.01 | 0.01 |
| max_grad_norm | 1.0 | 1.0 |
| LoRA r / α / dropout | — | 32 / 64 / 0.1 |
| 기타 설정 | MobiBench와 동일 | MobiBench와 동일 |

### Training Recipe 근거

`.claude/researchs/vlm-gui-finetuning-research.md` + gWorld(Qwen3-VL-8B, 260K, lr=2e-7, cosine, warmup=0.01) / Code2World(Qwen3-VL-8B SFT, lr=1e-5) / MobileDreamer(Qwen3-8B LoRA, HP 대부분 비공개) 문헌을 참고해 데이터셋 규모 차이를 반영한 분기 설계.
- **MobiBench (≈3k 샘플)**: 소규모이므로 `epochs=5`로 충분히 학습, `save_strategy=epoch`로 에폭 단위 체크포인트. `warmup_ratio=0.05`로 안정적 워밍업.
- **AndroidControl (≈71k/92k 샘플, 2026-04-13 재검토)**: 대규모 데이터셋. Stage 1 lr=1e-5(Code2World와 정렬), Stage 2 lr=5e-5(research doc 권장 5e-5~1e-4 하한, Qwen-GUI-3B Stage 2와 정렬). 데이터 규모 증가분을 epoch 상향으로 흡수하여 `epochs=3`으로 학습 여유 확보 — `load_best_model_at_end=true`가 과적합 구간을 자동 회피. LoRA rank 상향(r=16→32, α=32→64)은 50K-100K 샘플 구간 권장 rank 64-128의 보수 선택. 대규모 test split(3,553/4,587) eval 부담은 `per_device_eval_batch_size=4`로 상쇄. Stage 2 accum을 4→8로 올려 effective batch를 gWorld/Code2World와 동일한 64로 맞춤. **save/eval strategy는 MB와 동일한 `epoch` 로 통일**(2026-04-13 최초엔 `steps(500)` 이었으나 관리 일관성 위해 변경) — 3 epoch 동안 checkpoint 3개만 생성되어 디스크 부담이 크게 줄고, `load_best_model_at_end` 는 동일 작동(선택 후보가 3개로 줄어드는 것은 trade-off).
- **공통 안정화**: `max_grad_norm=1.0`, `weight_decay=0.01`, `save_total_limit=5`, `load_best_model_at_end=true` + `metric_for_best_model=eval_loss`로 학습 실패/OOM 복구 및 best checkpoint 자동 선택을 보장.
- **Eval 전략**: 별도 validation split을 만들지 않고 기존 `{ds_prefix}_stage{n}_test` 데이터셋을 `eval_dataset`으로 재사용(train 중 best checkpoint 선택 + 이후 quantitative eval 모두 같은 set을 사용).

> Cell 3의 `_DATASET_CONFIG[ds_name]["stage{1,2}"]`에서 데이터셋×스테이지별 하이퍼파라미터 자유 조정 가능 (lr, epochs, warmup_ratio, save_strategy, save_steps, eval_strategy, eval_steps, gradient_accumulation_steps, per_device_eval_batch_size, lora_rank, lora_alpha, lora_dropout).

## Dependencies

- Python >= 3.10, CUDA >= 12.1
- torch >= 2.4.0, transformers >= 5.0.0, peft >= 0.18.0, deepspeed, vllm >= 0.8.2
- LLaMA-Factory (프레임워크)
- beautifulsoup4, munkres, nltk, rouge, jieba (평가 메트릭)

## Related

- **Monkey 프로젝트**: 상위 프로젝트. GUI Foundation Model 구축을 위한 전체 학습 파이프라인 및 데이터 수집 시스템. `../Monkey-Collector/PRD.md` 참조
- **학습된 모델 (HuggingFace)**:
  - MobiBench: `SaFD-00/qwen3-vl-8b-stage1-world-model`, `stage2-world-model`, `stage2-base`
  - AndroidControl: `SaFD-00/qwen3-vl-8b-ac-stage1-world-model`, `ac-stage2-world-model`, `ac-stage2-base`
