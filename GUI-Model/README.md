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

`gui-model.ipynb` Cell 3의 `DATASET_NAME` 변수로 데이터셋을 전환합니다:

| Dataset | Stage 1 | Stage 2 | Images | 규모 |
|---------|---------|---------|--------|------|
| **MobiBench** (기본) | 3,145건 | 3,655건 | 3,655개 | ~28 MB |
| **AndroidControl** | 34,948건 | 58,234건 | 20,129개 | ~479 MB |

각 데이터셋은 95:5 비율로 Train/Test Split됩니다. AndroidControl은 MobiBench 대비 ~15배 대규모이며, 하이퍼파라미터가 자동 조정됩니다.

## 평가 메트릭

### Stage 1: World Modeling

| Metric | Description |
|--------|-------------|
| eval_loss / Perplexity | Next token prediction loss |
| BLEU-4 | n-gram 기반 텍스트 유사도 |
| ROUGE-L | Longest Common Subsequence F1 |
| Exact Match | GT XML과 완전 일치 비율 |
| Hungarian EA | Element Accuracy (매칭수 / max(pred, gt)) |
| Hungarian F1 | Precision-Recall F1 Score |
| Hungarian Text | 매칭 쌍의 Jaccard 텍스트 유사도 평균 |
| Hungarian Idx | 매칭 쌍의 index 위치 정확도 (|diff| ≤ 2) |

> 헝가리안 알고리즘으로 pred-gt 간 최적 1:1 요소 매칭 후 정확도 산출

### Stage 2: Action Prediction

| Metric | Description |
|--------|-------------|
| Parse Rate | 출력 JSON 파싱 성공률 |
| Type Accuracy | Action type 일치율 |
| Bounds IoU | Bounding box 겹침 비율 |
| Params Accuracy | Action params 일치율 |
| **Overall Score** | Type × (0.5×IoU + 0.5×Params) |

## 학습 설정

### Stage 1: World Modeling (Full Fine-tuning)

| 항목 | MobiBench | AndroidControl |
|------|-----------|----------------|
| Method | Full (all parameters) | Full (all parameters) |
| Effective Batch | 64 (2 × 8 × 4 GPU) | 64 (2 × 8 × 4 GPU) |
| Learning Rate | 2e-5 (cosine) | 1e-5 (cosine) |
| Epochs | 3.0 | 1.0 |
| DeepSpeed | ZeRO-3 | ZeRO-3 |
| Hardware | A100 80GB × 4 | A100 80GB × 4 |

### Stage 2: Action Prediction (LoRA, 4-Way 비교)

| 항목 | MobiBench | AndroidControl |
|------|-----------|----------------|
| Method | LoRA (r=16, α=32, dropout=0.1) | 동일 |
| Effective Batch | 32 (1 × 16 × 2 GPU) | 32 (1 × 16 × 2 GPU) |
| Learning Rate | 5e-5 (cosine, warmup=0.05) | 2e-5 (cosine, warmup=0.05) |
| Epochs | 2.0 | 1.0 |
| Hardware | RTX 5090 32GB × 2 | RTX 5090 32GB × 2 |

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
├── MobiBench/
│   ├── images/                      # 모바일 UI 스크린샷 (3,655개 PNG)
│   ├── gui-model_stage1.jsonl       # Stage 1 (3,145건)
│   └── gui-model_stage2.jsonl       # Stage 2 (3,655건)
└── AndroidControl/
    ├── images/                      # 스크린샷 (episode_{id}_step_{num}.png)
    ├── gui-model_stage1.jsonl       # Stage 1 (34,948건)
    └── gui-model_stage2.jsonl       # Stage 2 (58,234건)
```

### 3. 데이터 Split

학습 전 Train/Test Split을 사전 수행합니다:

```bash
python scripts/split_data.py --dataset MobiBench
python scripts/split_data.py --dataset AndroidControl
```

옵션: `--ratio 0.95` (기본), `--seed 42` (기본)

### 4. 학습 실행

`gui-model.ipynb` 노트북의 셀을 순서대로 실행합니다:

1. **Section 0**: 환경 설정 및 LLaMA-Factory 설치
2. **Cell 3**: `DATASET_NAME` 설정 (`"MobiBench"` 또는 `"AndroidControl"`)
3. **Section 1-2**: Stage 1/2 데이터 등록 (상대 경로로 dataset_info.json에 등록, 파일 복사 없음)
4. **Section 3**: Stage 1 학습 (Exp-1, Full FT, DeepSpeed ZeRO-3)
5. **Section 4**: Stage 1 모델 Merge & HuggingFace 업로드
6. **Section 5**: Stage 1 평가 (Exp-1 vs Baseline Zero-shot)
7. **Section 6**: Stage 2 학습 (Exp-2, Exp-3, Exp-4)
8. **Section 7**: Stage 2 모델 Merge & HuggingFace 업로드
9. **Section 8**: Stage 2 평가 (4-Way + Baseline Zero-shot 비교)

> **데이터셋 전환**: Cell 3의 `DATASET_NAME`만 변경하면 모든 경로, 데이터셋명, HF 모델 ID, 하이퍼파라미터가 자동 전환됩니다.

## 프로젝트 구조

```
GUI-Model/
├── gui-model.ipynb    # 전체 파이프라인 (데이터 준비 → 학습 → 평가 → 배포)
├── PRD.md             # Product Requirements Document
├── README.md          # 이 파일
├── requirements.txt   # Python 의존성
├── scripts/           # 유틸리티 스크립트
│   ├── split_data.py                    # Train/Test Split CLI
│   └── extract_androidcontrol_images.py # AndroidControl 이미지 추출
├── data/              # 데이터셋 (git 미포함)
│   ├── MobiBench/     # MobiBench 데이터셋 (images/, stage1/2 JSONL)
│   └── AndroidControl/ # AndroidControl 데이터셋 (images/, stage1/2 JSONL)
└── .env.example       # 환경변수 템플릿
```

## References

- [Code2World](https://arxiv.org/abs/2602.09856) (GD-ML) - GUI World Model via Renderable Code Generation
- [gWorld](https://arxiv.org/abs/2602.01576) (TrillionLabs) - Generative Visual Code Mobile World Models
- [MobileDreamer](https://arxiv.org/abs/2601.04035) - Generative Sketch World Model for GUI Agent

## 라이선스

이 프로젝트는 Apache License 2.0을 따릅니다.
