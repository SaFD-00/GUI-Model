# GUI-Model

모바일 UI의 상태 전이(World Modeling)와 액션 예측(Action Prediction)을 위한 2-Stage Fine-tuning 파이프라인.

**핵심 가설**: GUI World Modeling으로 사전학습된 VLM은 Action Prediction에서 더 빠르게 수렴한다.

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
| GUI-Model_stage1_train | 1 | 3,087 | 전체 (OpenApp 포함) |
| GUI-Model_stage1_NoOpenApp_train | 1 | 2,073 | OpenApp 제외 |
| GUI-Model_stage2_train | 2 | 3,655 | Action Prediction |
| Images | 공유 | 3,655 | 모바일 UI 스크린샷 (PNG) |

## 학습 설정

### Stage 1: World Modeling (Full Fine-tuning)

| 항목 | 값 |
|------|-----|
| Method | Full (all parameters) |
| Dataset | GUI-Model_stage1_NoOpenApp_train (2,073건) |
| Effective Batch | 16 (1 x 8 x 2 GPU) |
| Learning Rate | 2e-5 (constant) |
| Epochs | 3.0 |
| DeepSpeed | ZeRO-3 |
| Hardware | A100 80GB x 2 |

### Stage 2: Action Prediction (LoRA, 3-Way 비교)

World Model 사전학습이 Action Prediction 성능에 미치는 영향을 검증하기 위한 3-Way 비교 실험.

| ID | Base Model | HuggingFace ID |
|----|------------|----------------|
| S2-1 | Qwen3-VL-8B (Baseline) | `Qwen/Qwen3-VL-8B-Instruct` |
| S2-2 | Code2World-8B | `GD-ML/Code2World` |
| S2-3 | gWorld-8B | `trillionlabs/gWorld-8B` |

**공통 설정:**

| 항목 | 값 |
|------|-----|
| Method | LoRA (r=16, α=32, dropout=0.1) |
| Dataset | GUI-Model_stage2_train (3,655건) |
| Effective Batch | 16 (4 x 2 x 2 GPU) |
| Learning Rate | 5e-5 (cosine, warmup=0.05) |
| Epochs | 3.0 |
| Hardware | A100 80GB x 2 |

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
├── gui-model_stage1_NoOpenApp.jsonl # Stage 1 (OpenApp 제외)
└── gui-model_stage2.jsonl           # Stage 2
```

### 3. 학습 실행

`gui-model.ipynb` 노트북의 셀을 순서대로 실행합니다:

1. **Section 0**: 환경 설정 및 LLaMA-Factory 설치
2. **Section 1**: Stage 1 데이터 등록
3. **Section 1.5**: Stage 2 데이터 변환 및 등록
4. **Section 2**: Stage 1 학습 (Full FT, DeepSpeed ZeRO-3)
5. **Section 3**: Stage 1 모델 Merge & HuggingFace 업로드
6. **Section 4**: Stage 2 학습 (LoRA, 3-Way 비교)
7. **Section 6**: Stage 2 최적 모델 Merge & 업로드

## 프로젝트 구조

```
GUI-Model/
├── gui-model.ipynb    # 전체 파이프라인 (데이터 준비 → 학습 → 배포)
├── data/              # 데이터셋 (git 미포함)
│   ├── images/        # 모바일 UI 스크린샷
│   ├── gui-model_stage1.jsonl
│   ├── gui-model_stage1_NoOpenApp.jsonl
│   └── gui-model_stage2.jsonl
└── README.md
```

> `data/`와 `LlamaFactory/`는 용량 문제로 저장소에 포함되지 않습니다.

## 라이선스

이 프로젝트는 Apache License 2.0을 따릅니다.
