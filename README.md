# GUI-Model

모바일 UI의 상태 전이(World Modeling)와 액션 예측(Action Prediction)을 위한 2-Stage Fine-tuning 파이프라인.

**핵심 가설**: GUI World Modeling으로 사전학습된 VLM은 Action Prediction에서 더 높은 성능을 보인다.

## 개요

```
Stage 1: World Modeling          Stage 2: Action Prediction
┌─────────────────────┐          ┌──────────────────────────┐
│ UI State (XML)      │          │ Screenshot (Image)       │
│ + Action            │          │ + UI State (XML)         │
│ + Screenshot        │          │ + Task Description       │
│         ↓           │          │         ↓                │
│ Next UI State (XML) │          │ Action (JSON)            │
└─────────────────────┘          └──────────────────────────┘
```

- **Stage 1**: UI 상태(XML)와 액션이 주어졌을 때, 다음 UI 상태를 예측하는 World Model 학습
- **Stage 2**: 스크린샷 + UI 상태 + 태스크 설명으로부터 수행할 액션을 예측

## 4-Way 비교 실험

World Model 사전학습이 Action Prediction 성능에 미치는 영향을 검증하기 위한 4-Way ablation study.

| Exp | Stage 1 | Stage 2 | Base Model | 목적 |
|-----|---------|---------|------------|------|
| Exp-1 | Full FT | — | Qwen3-VL-8B-Instruct | World Model 학습 및 평가 |
| Exp-2 | Full FT → Merge | LoRA FT | SaFD-00/qwen3-vl-8b-stage1-world-model | World Model + Action Prediction |
| Exp-3 | — | LoRA FT | Qwen3-VL-8B-Instruct | Baseline (Control Group) |
| Exp-4 | — | LoRA FT | trillionlabs/gWorld-8B | 기존 World Model |

**핵심 비교:**
- Exp-2 vs Exp-3 → 자체 World Modeling 사전학습 효과 검증
- Exp-3 vs Exp-4 → 기존 World Model(gWorld)의 Action Prediction 기여도
- Exp-2 vs Exp-4 → 자체 vs 기존 World Model 비교
- Baseline (Zero-shot) → Stage 1/2 모두에서 Qwen3-VL-8B-Instruct 원본 대비 성능 측정

## 모델

| 항목 | 값 |
|------|-----|
| Base Model | [Qwen/Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) |
| Template | qwen3_vl_nothink |
| Vision Tower | Frozen |
| Framework | [LLaMA-Factory](https://github.com/hiyouga/LlamaFactory) |

## 데이터셋

| Dataset | Stage | Entries | 설명 |
|---------|-------|---------|------|
| GUI-Model_stage1_train | 1 | ~2,988 | World Modeling (Train 95%) |
| GUI-Model_stage1_test | 1 | ~157 | World Modeling (Test 5%) |
| GUI-Model_stage2_train | 2 | ~2,832 | Action Prediction (Train 90%) |
| GUI-Model_stage2_test | 2 | ~315 | Action Prediction (Test 10%) |
| Images | 공유 | 3,655 | 모바일 UI 스크린샷 (PNG) |

## 학습 설정

### Stage 1: World Modeling (Full Fine-tuning)

| 항목 | 값 |
|------|-----|
| Method | Full (all parameters) |
| Dataset | GUI-Model_stage1_train (~2,988건) |
| Effective Batch | 64 (2 × 8 × 4 GPU) |
| Learning Rate | 2e-5 (cosine, warmup=0.1) |
| Epochs | 3.0 |
| DeepSpeed | ZeRO-3 |
| Hardware | A100 80GB × 4 |

### Stage 2: Action Prediction (LoRA, 4-Way 비교)

**공통 설정:**

| 항목 | 값 |
|------|-----|
| Method | LoRA (r=16, α=32, dropout=0.1) |
| Dataset | GUI-Model_stage2_train (~2,832건) |
| Effective Batch | 32 (2 × 8 × 2 GPU) |
| Learning Rate | 5e-5 (cosine, warmup=0.05) |
| Epochs | 1.0 |
| Hardware | RTX 5090 32GB × 2 |

**Stage 2 실험:**

| ID | Base Model | HuggingFace ID |
|----|------------|----------------|
| Exp-2 | Stage 1 Merged | `SaFD-00/qwen3-vl-8b-stage1-world-model` |
| Exp-3 | Qwen3-VL-8B (Baseline) | `Qwen/Qwen3-VL-8B-Instruct` |
| Exp-4 | gWorld-8B | `trillionlabs/gWorld-8B` |

## 사용법

### 1. 환경 설정

```bash
git clone https://github.com/SaFD-00/GUI-Model.git
cd GUI-Model
```

### 2. 데이터 준비

[Google Drive](https://drive.google.com/drive/folders/1w55poUT6Sj2HrFERBFw4FeX_Rqja_mr4?usp=sharing)에서 데이터를 다운로드하여 `data/` 디렉토리에 배치합니다:

```
data/
├── images/                          # 모바일 UI 스크린샷 (3,655개 PNG)
├── gui-model_stage1.jsonl           # Stage 1 전체
└── gui-model_stage2.jsonl           # Stage 2
```

### 3. 학습 실행

`gui-model.ipynb` 노트북의 셀을 순서대로 실행합니다:

1. **Section 0**: 환경 설정 및 LLaMA-Factory 설치
2. **Section 1-2**: Stage 1/2 데이터 등록
3. **Section 3**: Stage 1 학습 (Exp-1, Full FT, DeepSpeed ZeRO-3)
4. **Section 4**: Stage 1 평가 (Exp-1 vs Baseline Zero-shot)
5. **Section 5**: Stage 1 모델 Merge & HuggingFace 업로드
6. **Section 6**: Stage 2 학습 (Exp-2, Exp-3, Exp-4)
7. **Section 7**: Stage 2 평가 (4-Way + Baseline Zero-shot 비교)
8. **Section 8**: Stage 2 모든 모델 Merge & 업로드

## 프로젝트 구조

```
GUI-Model/
├── gui-model.ipynb    # 전체 파이프라인 (데이터 준비 → 학습 → 평가 → 배포)
├── PRD.md             # Product Requirements Document
├── README.md          # 이 파일
├── data/              # 데이터셋 (git 미포함)
│   ├── images/        # 모바일 UI 스크린샷
│   ├── gui-model_stage1.jsonl
│   └── gui-model_stage2.jsonl
└── .env.example       # 환경변수 템플릿
```

## References

- [Code2World](https://arxiv.org/abs/2602.09856) (GD-ML) - GUI World Model via Renderable Code Generation
- [gWorld](https://arxiv.org/abs/2602.01576) (TrillionLabs) - Generative Visual Code Mobile World Models
- [MobileDreamer](https://arxiv.org/abs/2601.04035) - Generative Sketch World Model for GUI Agent

## 라이선스

이 프로젝트는 Apache License 2.0을 따릅니다.
