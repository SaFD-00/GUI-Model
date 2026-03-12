# GUI-Model: Product Requirements Document

## 1. 프로젝트 개요

| 항목 | 내용 |
|------|------|
| 프로젝트명 | GUI-Model |
| 목적 | 모바일 GUI World Modeling이 Action Prediction 성능에 미치는 영향 정량 검증 |
| 연구 유형 | Ablation Study (4-Way Comparison) |
| Base Model | Qwen/Qwen3-VL-8B-Instruct |
| Framework | LLaMA-Factory |
| Hardware | A100 80GB × 4 |

## 2. 연구 동기

### 2.1 Background

모바일 GUI Agent 분야에서 Vision-Language Model(VLM)은 스크린샷을 이해하고 사용자 의도에 맞는 액션을 수행하는 핵심 기술로 발전하고 있다. 최근 연구들은 GUI의 **상태 전이를 예측하는 World Model**을 학습함으로써, Agent가 환경의 동작 원리를 이해하고 더 정확한 액션을 예측할 수 있다는 가능성을 제시하고 있다.

### 2.2 관련 연구

#### Code2World (GD-ML, 2025)
- **접근**: GUI 상태 전이를 렌더링 가능한 HTML 코드로 생성
- **Base Model**: Qwen3-VL-8B-Instruct
- **방법론**: SFT로 코드 생성 능력을 부여한 뒤, Render-Aware Reinforcement Learning(RARL)로 시각적 일관성 향상
- **데이터**: AndroidCode (80K+ 고품질 화면-액션 쌍)
- **성과**: Gemini-2.5-Flash의 AndroidWorld 내비게이션 성능 +9.5% 향상

#### gWorld (TrillionLabs, 2025)
- **접근**: 최초의 오픈웨이트 단일 VLM 기반 모바일 GUI World Model
- **특징**: 다음 GUI 상태를 실행 가능한 웹 코드로 예측, 구조적 오류 <1%
- **모델**: gWorld-8B, gWorld-32B
- **성과**: Qwen3-VL-8B 대비 +45.7%, 더 큰 모델(Llama 4 402B 등)도 능가

#### MobileDreamer (2025)
- **접근**: Textual Sketch World Model (TSWM) — GUI 상태를 텍스트 스케치로 표현
- **특징**: Element-level matching loss를 통한 정밀한 UI 요소 예측
- **성과**: Android World 태스크 성공률 +5.25% 향상

### 2.3 Research Gap

기존 연구들은 World Model의 **예측 품질 자체**(다음 상태 예측 정확도)에 초점을 맞추고 있으나, World Model 사전학습이 **downstream Action Prediction 태스크에 실제로 도움이 되는지** 정량적으로 검증한 연구는 부족하다. 본 프로젝트는 동일 베이스 모델에 대해 World Model 사전학습 유무에 따른 Action Prediction 성능 차이를 4-Way ablation study로 검증한다.

## 3. 가설

| ID | 가설 | 검증 방법 |
|----|------|----------|
| H1 | GUI World Modeling으로 사전학습된 VLM은 동일 베이스 대비 Action Prediction에서 더 높은 성능을 보인다 | Exp-2 vs Exp-3 Overall Score 비교 |
| H2 | 자체 World Model(Exp-2)은 기존 World Model(Exp-4, gWorld)과 비견되는 성능을 보인다 | Exp-2 vs Exp-4 Overall Score 비교 |
| H0 | World Modeling 사전학습은 Action Prediction 성능에 유의미한 차이를 만들지 않는다 | H1 기각 실패 시 채택 |

## 4. 실험 설계

### 4.1 4-Way Comparison

| Exp | Stage 1 (World Modeling) | Stage 2 (Action Prediction) | Base Model | 목적 |
|-----|--------------------------|----------------------------|------------|------|
| Exp-1 | Full FT | — | Qwen3-VL-8B-Instruct | World Model 품질 평가 |
| Exp-2 | Full FT → Merge | LoRA FT | SaFD-00/qwen3-vl-8b-gui | 핵심 실험: World Model → Action |
| Exp-3 | — | LoRA FT | Qwen3-VL-8B-Instruct | Control Group (Baseline) |
| Exp-4 | — | LoRA FT | trillionlabs/gWorld-8B | 기존 World Model 비교 |

**Baseline**: Qwen3-VL-8B-Instruct (Zero-shot, 학습 없음)을 Stage 1/2 모두에서 평가

### 4.2 변수 통제

Stage 2 실험 간 공정성을 위해 다음 변수를 통일:

| 항목 | 값 | 비고 |
|------|-----|------|
| Fine-tuning Method | LoRA (r=16, α=32, dropout=0.1) | 동일 |
| Dataset | GUI-Model_stage2_train (~3,472건) | 동일 분할 |
| Effective Batch Size | 32 | 동일 |
| Learning Rate | 5e-5 (cosine, warmup=0.05) | 동일 |
| Epochs | 1.0 | 동일 |
| Vision Tower | Frozen | 동일 |
| Template | qwen3_vl_nothink | 동일 |
| cutoff_len | 8,192 | 동일 |

### 4.3 Training Pipeline

```
[Stage 1]                    [Stage 2]                    [Evaluation]
Qwen3-VL-8B                  Merged Model (Exp-2)
    │                            │
    ├─ Full FT (3 epoch) ──►  Merge ──► LoRA FT ──────►  Stage 2 Metrics
    │                                                        │
    ├─ (skip) ──────────────────────►  LoRA FT (Exp-3) ──►  Stage 2 Metrics
    │                                                        │
    └─ (gWorld-8B) ─────────────────►  LoRA FT (Exp-4) ──►  Stage 2 Metrics
```

### 4.4 Evaluation Protocol

#### Stage 1 평가 (World Modeling)

| Metric | Description |
|--------|-------------|
| eval_loss | Next token prediction loss |
| Perplexity | exp(eval_loss) |
| BLEU-4 | 생성 XML vs GT XML n-gram 유사도 |
| ROUGE-L | 최장 공통 부분 문자열 기반 유사도 |
| Element Accuracy | XML 요소(태그+속성) 일치율 |
| Index Coverage | XML 인덱스 커버리지 |

비교: Exp-1 (Fine-tuned) vs Baseline (Zero-shot)

#### Stage 2 평가 (Action Prediction)

| Metric | Formula | Description |
|--------|---------|-------------|
| Parse Rate | 유효 JSON / 전체 | 출력 파싱 성공률 |
| Type Accuracy | 정확 type / 전체 | Action type 일치율 |
| Bounds IoU | IoU(GT, Pred) | Bounding box 겹침 비율 |
| Params Accuracy | 정확 params / 전체 | Action params 일치율 |
| **Overall Score** | Type × (0.5×IoU + 0.5×Params) | **종합 점수** |

비교: Base (Zero-shot) vs Exp-2 vs Exp-3 vs Exp-4

## 5. 데이터셋

### 5.1 출처

모바일 UI 인터랙션 데이터로부터 구성. 각 샘플은 스크린샷(PNG) + UI 계층구조(XML) + 액션 정보를 포함.

### 5.2 Stage 1 (World Modeling)

| 항목 | 값 |
|------|-----|
| 원본 데이터 | gui-model_stage1.jsonl (3,145건) |
| Train Split | ~2,988건 (95%) |
| Test Split | ~157건 (5%) |
| Split Method | Random, seed=42 |
| Format | ShareGPT (multimodal) |
| Task | UI State (XML) + Action → Next UI State (XML) |

### 5.3 Stage 2 (Action Prediction)

| 항목 | 값 |
|------|-----|
| 원본 데이터 | gui-model_stage2.jsonl (3,655건) |
| Train Split | ~3,472건 (95%) |
| Test Split | ~183건 (5%) |
| Split Method | Stratified by action type, seed=42 |
| Format | ShareGPT (multimodal) |
| Task | Screenshot + UI State + Task → Action (JSON) |

### 5.4 이미지

- 3,655개 모바일 UI 스크린샷 (PNG)
- Stage 1/2 공유
- image_max_pixels: 4,233,600

## 6. 하드웨어 및 인프라

| 항목 | 값 |
|------|-----|
| GPU | NVIDIA A100 80GB × 4 |
| 분산 학습 | torchrun (NPROC_PER_NODE=4) |
| 메모리 최적화 | DeepSpeed ZeRO Stage 3 |
| 정밀도 | bf16 (bfloat16) |
| Gradient Checkpointing | Enabled |
| Framework | LLaMA-Factory |
| Inference | vLLM |

### Stage 1 하이퍼파라미터 (Full FT)

| Parameter | 값 | 근거 |
|-----------|-----|------|
| per_device_train_batch_size | 2 | VLM 이미지 메모리 고려, ZeRO-3에서 안전 |
| gradient_accumulation_steps | 8 | effective batch 64 확보 |
| learning_rate | 2e-5 | batch 4배 증가(16→64)에 sqrt scaling (√4=2) |
| lr_scheduler_type | cosine | constant 대비 수렴 안정성 우수, Qwen3 공식 권고 |
| warmup_ratio | 0.1 | LR 증가 + batch 증가에 따른 초기 안정화 |
| num_train_epochs | 3.0 | 데이터 ~3K, 충분한 학습 |
| weight_decay | 0.01 | 표준 정규화 |

### Stage 2 하이퍼파라미터 (LoRA)

| Parameter | 값 | 근거 |
|-----------|-----|------|
| per_device_train_batch_size | 4 | LoRA는 메모리 효율적, per_device=4 가능 |
| gradient_accumulation_steps | 2 | effective batch 32 |
| learning_rate | 5e-5 | LoRA 표준 |
| lr_scheduler_type | cosine | 수렴 안정성 |
| warmup_ratio | 0.05 | 표준 |
| num_train_epochs | 1.0 | 과적합 방지 |

## 7. 성공 기준

### Primary (핵심)

- **Exp-2 Overall Score > Exp-3 Overall Score**: 자체 World Model 사전학습이 Action Prediction 성능을 향상시킴

### Secondary (부차)

- **Exp-2 수렴 속도**: Exp-3 대비 training loss가 더 빠르게 감소
- **Exp-1 Stage 1 메트릭**: Baseline(Zero-shot) 대비 유의미한 BLEU/ROUGE-L 향상

### Exploratory (탐색)

- **Exp-4 vs Exp-3**: 기존 World Model(gWorld)도 Action Prediction에 도움이 되는가
- **Exp-2 vs Exp-4**: 자체 World Model이 기존 World Model과 비교해 어떤 액션 타입에서 우위를 보이는가

## 8. 타임라인

| Phase | Task | 예상 소요 |
|-------|------|----------|
| Phase 1 | 데이터 준비 및 검증 | 1일 |
| Phase 2 | Stage 1 Full FT (Exp-1) | 2-3시간 |
| Phase 3 | Stage 1 평가 & Merge & Upload | 1시간 |
| Phase 4 | Stage 2 LoRA FT × 3 (Exp-2, 3, 4) | 3-4시간 |
| Phase 5 | Stage 2 평가 (4-Way + Baseline) | 1시간 |
| Phase 6 | 분석 및 리포트 작성 | 1일 |

## 9. 리스크 및 완화 방안

| 리스크 | 영향 | 완화 방안 |
|--------|------|----------|
| Stage 1 World Model 품질 부족 | Exp-2가 Exp-3보다 성능이 낮을 수 있음 | Stage 1 평가에서 조기 확인, 하이퍼파라미터 조정 |
| gWorld의 학습 데이터 분포 차이 | Exp-4와의 비교가 unfair할 수 있음 | Per-Type 분석으로 액션 타입별 강약점 파악 |
| 데이터셋 규모 (~3K) 제한 | 통계적 유의성 확보 어려움 | Per-Type 메트릭으로 세분화 분석, confidence interval 보고 |
| A100 메모리 OOM | per_device_batch_size=2에서 OOM 발생 가능 | gradient checkpointing 활성화, per_device=1로 fallback 후 grad_accum 조정 |

## 10. Deliverables

| 산출물 | 위치 | 설명 |
|--------|------|------|
| 학습된 모델 (Stage 1) | `SaFD-00/qwen3-vl-8b-gui` | World Model |
| 학습된 모델 (Exp-2) | `SaFD-00/qwen3-vl-8b-gui-world-model` | World Model + Action |
| 학습된 모델 (Exp-3) | `SaFD-00/qwen3-vl-8b-gui-baseline` | Baseline |
| 학습된 모델 (Exp-4) | `SaFD-00/qwen3-vl-8b-gui-gworld` | gWorld + Action |
| 평가 리포트 | `outputs/stage*_predict_*/evaluation_report.md` | 모델별 상세 메트릭 |
| 시각화 차트 | `outputs/stage2_evaluation.png` | 4-Way 비교 차트 |
| 실행 노트북 | `gui-model.ipynb` | 전체 파이프라인 |
