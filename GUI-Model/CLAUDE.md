# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GUI-Model은 모바일 GUI **World Modeling이 Action Prediction 성능에 미치는 영향**을 정량 검증하는 Ablation Study(4-Way Comparison) 프로젝트이다. Qwen3-VL-8B-Instruct를 Base Model로, LLaMA-Factory 프레임워크에서 2-Stage fine-tuning 파이프라인을 실행한다.

- **Stage 1 (World Modeling)**: UI 상태(XML) + 액션 + 스크린샷 → 다음 UI 상태(XML) 예측 (Full FT)
- **Stage 2 (Action Prediction)**: 스크린샷 + UI 상태 + 태스크 → 액션(JSON) 예측 (LoRA FT)

**핵심 가설**: World Modeling 사전학습이 downstream Action Prediction 성능을 향상시킨다.

이 저장소는 상위 Monkey 프로젝트(GUI Foundation Model 구축)의 **실험 검증 파트**에 해당한다. 학습 및 평가는 완료된 상태이다.

## 4-Way Experiment Design

| Exp | Stage 1 | Stage 2 | Base Model | 목적 |
|-----|---------|---------|------------|------|
| Exp-1 | Full FT | — | Qwen3-VL-8B-Instruct | World Model 품질 평가 |
| Exp-2 | Full FT → Merge | LoRA FT | SaFD-00/qwen3-vl-8b-stage1-world-model | 핵심: World Model → Action |
| Exp-3 | — | LoRA FT | Qwen3-VL-8B-Instruct | Control Group (Baseline) |
| Exp-4 | — | LoRA FT | trillionlabs/gWorld-8B | 기존 World Model 비교 |

**Baseline**: Qwen3-VL-8B-Instruct Zero-shot (학습 없음)

## Commands

```bash
# LLaMA-Factory 기반 학습 (gui-model.ipynb 셀 순서대로 실행 권장)

# Stage 1: World Modeling Full FT (A100 80GB × 4)
cd LlamaFactory
FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=4 \
  llamafactory-cli train examples/train_custom/GUI-Model/stage1_full/qwen3_vl_8b_gui.yaml

# Stage 1 평가: eval_loss
llamafactory-cli eval examples/train_custom/GUI-Model/stage1_eval/eval_loss.yaml

# Stage 1 평가: predict (생성 기반)
llamafactory-cli train examples/train_custom/GUI-Model/stage1_eval/predict.yaml

# Stage 2: Action Prediction LoRA FT (RTX 5090 32GB × 2)
# Exp-2, Exp-3, Exp-4 각각 별도 YAML로 실행 (model_name_or_path만 다름)
FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=2 \
  llamafactory-cli train examples/train_custom/GUI-Model/stage2_lora/<exp_yaml>

# Stage 2 평가: vLLM 배치 추론 + 커스텀 메트릭
# gui-model.ipynb Section 7 참조
```

## Architecture

### 실행 파이프라인

```
[gui-model.ipynb]  ──  전체 파이프라인 오케스트레이션 (Jupyter 노트북)
       │
       ├── Section 0: 환경 설정, LLaMA-Factory 설치
       ├── Section 1-2: 데이터 등록 (dataset_info.json에 추가)
       ├── Section 3: Stage 1 학습 (Full FT, DeepSpeed ZeRO-3)
       ├── Section 4: Stage 1 평가 (eval_loss + predict + Hungarian Matching)
       ├── Section 5: Stage 1 모델 Merge & HuggingFace 업로드
       ├── Section 6: Stage 2 학습 (LoRA FT × 3 실험)
       ├── Section 7: Stage 2 평가 (4-Way + Baseline 비교)
       └── Section 8: Stage 2 모델 Merge & 업로드
```

### 핵심 컴포넌트

- **gui-model.ipynb**: 전체 파이프라인의 유일한 실행 엔트리포인트. 데이터 준비, 학습, 평가, 모델 배포를 순차 실행
- **LlamaFactory/**: LLaMA-Factory 프레임워크 (submodule 또는 클론). 학습/평가 엔진
- **LlamaFactory/examples/train_custom/GUI-Model/**: 학습/평가 YAML 설정 파일
  - `stage1_full/`: Stage 1 Full FT 설정
  - `stage1_eval/`: Stage 1 평가 (eval_loss.yaml, predict.yaml)
  - `stage2_lora/`: Stage 2 LoRA 설정 (Exp-2, Exp-3, Exp-4)
  - `stage2_eval/`: Stage 2 평가 설정
- **LlamaFactory/outputs/**: 학습 체크포인트 및 평가 결과
  - `stage1_eval/eval_loss/`: Stage 1 loss 메트릭
  - `stage1_eval/hungarian_matching/`: Stage 1 요소 수준 메트릭
  - `stage2_eval/{base,lora_base,lora_world_model,lora_gworld}/`: 4-Way 평가 리포트
- **.claude/reference/metrics/**: 커스텀 평가 메트릭 구현
  - `metric.py`: LLaMA-Factory SFT eval에 BLEU, ROUGE, Hungarian 통합한 커스텀 메트릭
  - `hungarian_metric.py`: BeautifulSoup + Munkres 알고리즘 기반 XML 요소 1:1 매칭 정확도

## Data

### 데이터셋 위치

```
data/
├── images/                          # 모바일 UI 스크린샷 (3,655개 PNG)
├── gui-model_stage1.jsonl           # Stage 1 전체 (3,145건)
└── gui-model_stage2.jsonl           # Stage 2 전체 (3,655건)

LlamaFactory/data/GUI-Model/        # LLaMA-Factory 데이터 등록 (심볼릭 링크 또는 복사)
├── gui-model_stage1_train.jsonl     # Stage 1 Train (95%)
├── gui-model_stage1_test.jsonl      # Stage 1 Test (5%)
├── gui-model_stage2_train.jsonl     # Stage 2 Train (95%)
├── gui-model_stage2_test.jsonl      # Stage 2 Test (5%)
└── images/                          # 스크린샷
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
- **DeepSpeed**: Stage 1은 ZeRO-3 (A100 × 4), Stage 2는 ZeRO-2 (RTX 5090 × 2)
- **cutoff_len: 8192**: XML이 포함된 긴 입력을 처리하기 위한 설정
- **커스텀 메트릭 패치**: LLaMA-Factory의 `metric.py`에 Hungarian Matching을 통합하여 eval 시 자동 산출. 패치 가이드는 `.claude/reference/metrics/patch_guide_0315.txt` 참조

## Configuration

- **Stage 1 학습**: `LlamaFactory/examples/train_custom/GUI-Model/stage1_full/qwen3_vl_8b_gui.yaml`
- **Stage 1 평가**: `LlamaFactory/examples/train_custom/GUI-Model/stage1_eval/eval_loss.yaml`, `predict.yaml`
- **Stage 2 학습**: `LlamaFactory/examples/train_custom/GUI-Model/stage2_lora/` 내 실험별 YAML
- **Stage 2 평가**: `LlamaFactory/examples/train_custom/GUI-Model/stage2_eval/` 내 실험별 YAML
- **데이터 등록**: `LlamaFactory/data/dataset_info.json`에 GUI-Model 데이터셋 엔트리 추가 필요
- **환경변수**: `.env.example` 참조 (HuggingFace token 등)

## Key Hyperparameters

| 항목 | Stage 1 (Full FT) | Stage 2 (LoRA) |
|------|-------------------|----------------|
| batch × accum × GPU | 1 × 8 × 4 = 32 | 2 × 8 × 2 = 32 |
| learning_rate | 1e-5 | 5e-5 |
| epochs | 3.0 | 1.0 |
| LoRA r / α / dropout | — | 16 / 32 / 0.1 |
| DeepSpeed | ZeRO-3 | ZeRO-2 |
| image_max_pixels | 4,233,600 | 4,233,600 |

## Dependencies

- Python >= 3.10, CUDA >= 12.1
- torch >= 2.4.0, transformers >= 4.51.0, peft >= 0.18.0, deepspeed, vllm >= 0.8.2
- LLaMA-Factory (프레임워크)
- beautifulsoup4, munkres, nltk, rouge, jieba (평가 메트릭)

## Related

- **Monkey 프로젝트**: 상위 프로젝트. GUI Foundation Model 구축을 위한 전체 학습 파이프라인 및 데이터 수집 시스템. `../Monkey/PRD.md` 참조
- **학습된 모델 (HuggingFace)**:
  - `SaFD-00/qwen3-vl-8b-stage1-world-model` (Stage 1 Merged)
  - `SaFD-00/qwen3-vl-8b-stage2-world-model` (Exp-2)
  - `SaFD-00/qwen3-vl-8b-stage2-base` (Exp-3)
  - `SaFD-00/qwen3-vl-8b-stage2-gworld` (Exp-4)
