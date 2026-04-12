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

## 3-Way 비교 실험

World Model 사전학습이 Action Prediction 성능에 미치는 영향을 검증하기 위한 3-Way ablation study.

| Exp | Stage 1 | Stage 2 | Base Model | 목적 |
|-----|---------|---------|------------|------|
| Exp-1 | Full FT | — | Qwen3-VL-8B-Instruct | World Model 학습 및 평가 |
| stage1+stage2 | Full FT → Merge | LoRA FT | SaFD-00/qwen3-vl-8b-stage1-world-model | World Model + Action Prediction |
| stage2 | — | LoRA FT | Qwen3-VL-8B-Instruct | Baseline (Control Group) |

**핵심 비교:**
- stage2 vs stage1+stage2 → World Modeling 사전학습이 Action Prediction에 미치는 효과
- Baseline (Zero-shot) → Stage 1/2 모두에서 Qwen3-VL-8B-Instruct 원본 대비 성능 측정

## 모델

| 항목 | 값 |
|------|-----|
| Base Model | [Qwen/Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) |
| Template | qwen3_vl_nothink |
| Vision Tower | Frozen |
| Framework | [LLaMA-Factory](https://github.com/hiyouga/LlamaFactory) |

## 데이터셋

`gui-model.ipynb` Cell 3에서 두 데이터셋 모두 자동 설정됩니다:

| Dataset | Stage 1 | Stage 2 | Images | 규모 |
|---------|---------|---------|--------|------|
| **MobiBench** (기본) | 3,145건 | 3,147건 | 3,655개 | ~28 MB |
| **AndroidControl** (주력) | 71,047건 | 91,677건 | 20,129개 | ~479 MB |

각 데이터셋은 95:5 비율로 Train/Test Split됩니다.
- MobiBench: S1 2,987/158, S2 2,987/160
- AndroidControl: S1 67,494/3,553, S2 87,090/4,587

AndroidControl은 MobiBench 대비 Stage 1 ~23배 / Stage 2 ~29배 대규모이며, 하이퍼파라미터가 자동 조정됩니다 (2026-04-13 데이터 갱신 & 레시피 재검토).

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
| Learning Rate | 1e-5 (cosine) | **1e-5** (cosine) |
| Warmup Ratio | 0.05 | **0.03** |
| Epochs | 5 | **3** |
| weight_decay | 0.01 | 0.01 |
| max_grad_norm | 1.0 | 1.0 |
| save / eval strategy | epoch | steps (500) |
| save_total_limit | 5 | 5 |
| per_device_eval_batch_size | 1 | **4** |
| load_best_model_at_end | true (`metric_for_best_model=eval_loss`) | 동일 |
| DeepSpeed | ZeRO-3 | ZeRO-3 |
| Hardware | H100 80GB × 4 | H100 80GB × 4 |

### Stage 2: Action Prediction (LoRA, 3-Way 비교)

| 항목 | MobiBench | AndroidControl |
|------|-----------|----------------|
| Method | LoRA (r=16, α=32, dropout=0.1) | LoRA (**r=32, α=64**, dropout=0.1) |
| Effective Batch | 32 (2 × 4 × 4 GPU) | **64 (2 × 8 × 4 GPU)** |
| Learning Rate | 3e-5 (cosine, warmup=0.05) | **5e-5** (cosine, warmup=**0.03**) |
| Epochs | 5 | **3** |
| weight_decay | 0.01 | 0.01 |
| max_grad_norm | 1.0 | 1.0 |
| save / eval strategy | epoch | steps (500) |
| save_total_limit | 5 | 5 |
| per_device_eval_batch_size | 1 | **4** |
| load_best_model_at_end | true (`metric_for_best_model=eval_loss`) | 동일 |
| Hardware | H100 80GB × 4 | H100 80GB × 4 |

### Training Recipe 근거

학습 설정은 [vlm-gui-finetuning-research.md](.claude/researchs/vlm-gui-finetuning-research.md) + 아래 관련 연구의 training recipe를 참고하되, MB(소규모, ~3K)와 AC(대규모, ~71K/92K)의 **데이터 규모 차이**에 맞춰 분기했습니다. 2026-04-13 재검토 완료.

| 파라미터 | MB | AC | 근거 |
|----------|-----|-----|------|
| Epochs | 5 | 3 | MB는 gWorld/MobileDreamer 관례(5 epochs), AC는 데이터 규모에 맞춰 3 epochs로 수렴 여유 확보 (`load_best_model_at_end`이 과적합 방어) |
| S1 Learning Rate | 1e-5 | 1e-5 | Code2World SFT(1e-5)와 정렬. 기존 2e-5에서 하향하여 gWorld(2e-7) 방향 절충 |
| S2 Learning Rate | 3e-5 | 5e-5 | AC는 research doc 권장 LoRA LR 5e-5~1e-4의 하한, Qwen-GUI-3B Stage 2와 정렬 |
| Warmup | 0.05 | 0.03 | AC는 총 step 증가로 absolute warmup steps 유지 (0.03 × 3,164 ≈ 95 step, 0.03 × 4,083 ≈ 122 step) |
| LoRA r / α | 16 / 32 | 32 / 64 | AC는 50K-100K 샘플 권장 rank 64-128의 보수 선택, α=2r 규칙 적용 |
| Effective Batch (S2) | 32 | 64 | AC는 gWorld/Code2World와 동일한 64로 정렬 (accum 4→8) |
| save/eval strategy | epoch | steps (500) | MB 소규모는 epoch이 자연스럽고, AC 대규모는 step 추적이 운영상 유리 |
| per_device_eval_batch_size | 1 | 4 | AC는 test split(3,553/4,587)이 커서 eval 부담 상쇄 |
| Vision Encoder | Frozen | Frozen | Code2World, gWorld, MobileDreamer 모두 동결 |

**공통 안정화 옵션**: `weight_decay=0.01`(gWorld 정렬), `max_grad_norm=1.0`, `save_total_limit=5`, `load_best_model_at_end=true`(eval_loss 최소) — 학습 불안정/OOM 회복과 best checkpoint 자동 선택 보장. Eval은 별도 validation split을 만들지 않고 기존 `*_stage{n}_test` 데이터셋을 `eval_dataset`으로 재사용합니다.

**Stage 2 실험:**

| ID | Base Model | HuggingFace ID |
|----|------------|----------------|
| stage1+stage2 | Stage 1 Merged | `SaFD-00/qwen3-vl-8b-stage1-world-model` |
| stage2 | Qwen3-VL-8B (Baseline) | `Qwen/Qwen3-VL-8B-Instruct` |

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
│   └── gui-model_stage2.jsonl       # Stage 2 (3,147건)
└── AndroidControl/
    ├── gui-model_stage1.jsonl       # Stage 1 (71,047건)
    └── gui-model_stage2.jsonl       # Stage 2 (91,677건)
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
2. **Cell 3**: 프로젝트 경로(`BASE_DIR`) 설정 (두 데이터셋 모두 자동 설정)
3. **Section 1-2**: Stage 1/2 데이터 등록 (상대 경로로 dataset_info.json에 등록, 파일 복사 없음)
4. **Section 3**: Stage 1 학습 (Exp-1, Full FT, DeepSpeed ZeRO-3)
5. **Section 4**: Stage 1 모델 Merge & HuggingFace 업로드
6. **Section 5**: Stage 1 평가 (Exp-1 vs Baseline Zero-shot)
7. **Section 6**: Stage 2 학습 (stage2, stage1+stage2)
8. **Section 7**: Stage 2 모델 Merge & HuggingFace 업로드
9. **Section 8**: Stage 2 평가 (3-Way + Baseline Zero-shot 비교)

> Cell 3의 `CONFIGS` 딕셔너리에서 두 데이터셋의 경로, 하이퍼파라미터가 자동 설정됩니다.

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
