# GUI-Model Architecture

모바일 GUI World Modeling 이 Action Prediction 성능에 미치는 영향을 검증하기 위한 **Qwen3-VL-8B-Instruct 기반 2-Stage fine-tuning 파이프라인** (3-Way ablation 구조). LLaMA-Factory 프레임워크 위에서 동작한다.

---

## 1. 개요

### 1.1 시스템 요약

GUI-Model 은 Qwen3-VL-8B-Instruct 를 Base Model 로 사용하여 **Stage 1 (World Modeling, Full FT) → Stage 2 (Action Prediction, LoRA FT)** 두 단계를 순차 학습한다. 동일 데이터/설정 위에서 Stage 1 통과 여부만 달리한 3가지 변형(`Exp-1`, `stage1+stage2`, `stage2`)을 같은 Stage 2 파이프라인으로 돌려 **World Modeling 사전학습 기여도**를 분리 측정하는 ablation study 이다.

### 1.2 Monkey-Collector 파이프라인 내 위치

본 저장소는 상위 프로젝트 **Monkey-Collector** (범용 GUI Foundation Model 구축) 의 학습 파이프라인에서 **Phase 1 SFT 의 유효성을 선행 검증**하는 실험 모듈이다.

```
Monkey-Collector Pipeline (전체)
═══════════════════════════════════════════════════════════════════

Phase 1 - SFT
─────────────────────────────────────────────────────────────────

  Stage 1                          Stage 2
  GUI World Modeling          →    Task Finetuning
  (Full Finetuning)                (LoRA Finetuning)

  • Visual Grounding               • Short-horizon Tasks
  • OCR & Text Recognition          - 단일 액션 예측
  • Layout Understanding             - Element 클릭/타이핑
  • Screenshot Captioning          • Long-horizon Tasks
  • State Difference Detection       - 다단계 웹 네비게이션
  • Element Attribute QA             - 앱 조작 workflow


Phase 2 - RL
─────────────────────────────────────────────────────────────────

  RL Finetuning (GRPO)

  • Short-horizon reward: 정확한 액션 좌표/타입 매칭
  • Long-horizon reward: 태스크 완료 성공률
  • Curriculum: easy tasks → hard tasks
```

| MC Phase | 단계 | GUI-Model 대응 |
|-------------|------|----------------|
| Phase 1 - Stage 1 | GUI World Modeling (Full FT) | Exp-1 (World Model 학습 + 평가) |
| Phase 1 - Stage 2 | Task Finetuning (LoRA) | stage2, stage1+stage2 (Action Prediction 3-Way 비교) |
| Phase 2 | RL Finetuning (GRPO) | 미포함 (Monkey-Collector 에서 진행) |

### 1.3 Base Model: Qwen3-VL-8B-Instruct

| 항목 | 상세 |
|------|------|
| 아키텍처 | ViT visual encoder + Qwen LLM (Dense, 8B params) |
| 핵심 기술 | Interleaved-MRoPE (위치 인코딩), DeepStack (시각-텍스트 정렬) |
| 컨텍스트 | 256K tokens (최대 1M tokens 확장 가능) |
| GUI 지원 | 네이티브 GUI 에이전트 기능 — 요소 인식, 기능 이해, 도구 호출 |
| 시각 능력 | 32언어 OCR, 2D grounding, 동적 해상도, 멀티이미지/비디오 |
| 모델 ID | `Qwen/Qwen3-VL-8B-Instruct` |

### 1.4 기술적 차별점

- **3-Way Ablation Design**: 동일 Base Model 에 대해 World Model 사전학습 유무별 3가지 조건을 동일한 Stage 2 설정으로 비교
- **Full FT → LoRA 2-Stage 구조**: Stage 1 에서 전체 파라미터를 World Model 에 적응시킨 후, Stage 2 에서 LoRA 로 효율적 Action Prediction 학습
- **XML 기반 World Modeling**: GUI 상태를 HTML-style XML 로 표현하여 UI 구조적 이해 학습
- **Hungarian Matching 평가**: 요소 수준의 정량적 World Model 품질 측정 (Munkres 알고리즘 기반 최적 1:1 매칭)
- **LLaMA-Factory 기반**: 커스텀 학습 코드 없이 프레임워크 설정만으로 재현 가능한 실험
- **Post-training Best Epoch 자동 선택**: 학습 중 eval 을 비활성화하고, 학습 완료 후 `stage{1,2}_eval.sh` 가 checkpoint sweep 을 수행하여 Hungarian F1 (Stage 1) / Overall Score (Stage 2) 기준 winner 를 자동 기록

---

## 2. 시스템 아키텍처

### 2.1 계층 구조

```
┌──────────────────────────────────────────────────────────────────┐
│                       GUI-Model Pipeline                          │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                  Stage 1: World Modeling                     │  │
│  │                  (Full Fine-Tuning)                          │  │
│  │  UI State (XML) + Action + Screenshot → Next UI State (XML) │  │
│  └──────────────────────────┬──────────────────────────────────┘  │
│                             │ Merge                                │
│                             ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                 Stage 2: Action Prediction                   │  │
│  │                 (LoRA Fine-Tuning)                           │  │
│  │  Screenshot + UI State + Task → Action (JSON)               │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐    │
│  │ LLaMA-   │  │  Data    │  │  Eval    │  │  vLLM         │    │
│  │ Factory  │  │  Module  │  │  Module  │  │  Inference    │    │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 데이터 흐름

```
[모바일 UI 인터랙션 데이터]
    │
    ▼ JSONL 변환
[ShareGPT Multimodal Format] → 스크린샷(PNG) + UI 계층구조(XML) + 액션(JSON)
    │
    ├──► Stage 1: World Modeling Data
    │    (screenshot + UI XML + action) → (next UI XML)
    │
    └──► Stage 2: Action Prediction Data
         (screenshot + UI XML + task) → (action JSON)
```

### 2.3 핵심 컴포넌트

| 컴포넌트 | 역할 | 기술 스택 |
|----------|------|----------|
| LLaMA-Factory | 학습/평가 프레임워크 (SFT, LoRA) | transformers, PEFT, DeepSpeed |
| gui-model.ipynb | 전체 파이프라인 오케스트레이션 | Jupyter Notebook |
| scripts/*.sh | Train/Eval/Merge 재실행 파이프라인 | bash 4+, `set -euo pipefail` |
| scripts/_hungarian_eval.py | Stage 1 checkpoint 별 Hungarian/BLEU/ROUGE + winner 선택 | BeautifulSoup, Munkres, NLTK |
| scripts/_action_eval.py | Stage 2 checkpoint 별 Action 메트릭 + winner 선택 | Parse/Type/IoU/Params |
| Custom Metrics | Hungarian Matching 등 LLaMA-Factory 메트릭 패치 | BeautifulSoup, Munkres |
| vLLM Inference | 배치 추론 (체크포인트 sweep) | vLLM (≥0.8.2) |
| HuggingFace Hub | 모델 체크포인트 관리 및 배포 | huggingface_hub |

### 2.4 파이프라인 오케스트레이션

```
gui-model.ipynb
│
├── Section 0: 환경 설정 + LLaMA-Factory 설치 + YAML Configs 선행 생성
│              (Stage 1/2 Training YAML + Stage 1 Evaluation YAML 3종 작성.
│               Merge YAML 은 Section 5/8 내부에서 BEST_CHECKPOINT 반영 후 생성)
├── Section 1-2: 데이터 등록 (상대 경로로 dataset_info.json 등록, 이미지 symlink)
├── Section 3: Stage 1 학습 (Exp-1, Full FT, DeepSpeed ZeRO-3)
├── Section 4: Stage 1 평가 (Exp-1 vs Baseline Zero-shot, Hungarian F1 winner 선택 → BEST_CHECKPOINT)
├── Section 5: Stage 1 Merge YAML 생성 + HuggingFace 업로드
├── Section 6: Stage 2 학습 (stage2, stage1+stage2 — LoRA FT)
├── Section 7: Stage 2 평가 (3-Way + Baseline Zero-shot, Overall Score winner 선택 → BEST_CHECKPOINT)
└── Section 8: Stage 2 Merge YAML 생성 + HuggingFace 업로드
```

최초 실행은 notebook, 반복 실행은 `scripts/` 쉘 스크립트가 표준이다. 초기 환경 설치는 `bash scripts/setup.sh` 로 노트북 없이도 재현할 수 있다.

---

## 3. 3-Way 실험 구성

### 3.1 3-Way Comparison

| Exp | Stage 1 (World Modeling) | Stage 2 (Action Prediction) | Base Model | 목적 |
|-----|--------------------------|----------------------------|------------|------|
| Exp-1 | Full FT | — | Qwen3-VL-8B-Instruct | World Model 품질 평가 |
| stage1+stage2 | Full FT → Merge | LoRA FT | `SaFD-00/qwen3-vl-8b-stage1-world-model` | 핵심 실험: World Model → Action |
| stage2 | — | LoRA FT | Qwen3-VL-8B-Instruct | Control Group (Baseline) |

**Baseline**: Qwen3-VL-8B-Instruct Zero-shot (학습 없음) 을 Stage 1/2 모두에서 평가

### 3.2 변수 통제

Stage 2 실험 간 공정성을 위해 다음 변수를 통일 (MobiBench 기준):

| 항목 | 값 |
|------|-----|
| Fine-tuning Method | LoRA (r=16, α=32, dropout=0.1) |
| LoRA Target Modules | q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj |
| Vision Tower | Frozen |
| Template | qwen3_vl_nothink |
| cutoff_len | 16,384 |

같은 데이터셋 내에서 `stage2` / `stage1+stage2` 의 모든 hyperparameter, dataset split, 평가 파이프라인은 동일하며 오직 **base model** 만 교체된다.

### 3.3 Training Pipeline

```
[Stage 1]                    [Stage 2]                    [Evaluation]
Qwen3-VL-8B                  Merged Model (stage1+stage2)
    │                            │
    ├─ Full FT ──────────────► Merge ──► LoRA FT (stage1+stage2) ──►  Stage 2 Metrics
    │                                                                    │
    └─ (skip) ──────────────────────►  LoRA FT (stage2) ──────────────►  Stage 2 Metrics
```

### 3.4 Evaluation Protocol

#### Stage 1 (World Modeling)

| Metric | Description |
|--------|-------------|
| eval_loss | Next token prediction loss |
| Perplexity | exp(eval_loss) |
| BLEU-4 | 생성 XML vs GT XML n-gram 유사도 |
| ROUGE-L | 최장 공통 부분 문자열 기반 유사도 |
| Exact Match | GT XML 과 완전 일치 비율 |
| Hungarian EA | Element Accuracy (매칭수 / max(pred, gt)) |
| **Hungarian F1** | Precision-Recall F1 Score — **winner 선택 지표** |
| Hungarian Prec / Rec | Precision / Recall |
| Hungarian Text | 매칭 쌍의 Jaccard 텍스트 유사도 평균 |
| Hungarian Idx | 매칭 쌍의 index 위치 정확도 (\|diff\| ≤ 2) |

> **Hungarian Matching**: BeautifulSoup 로 XML 에서 interactive 요소를 추출한 뒤, Munkres(헝가리안) 알고리즘으로 pred-gt 간 최적 1:1 매칭을 수행하여 요소 수준의 정확도를 산출한다.

비교: Exp-1 (Fine-tuned) vs Baseline (Zero-shot)

#### Stage 2 (Action Prediction)

| Metric | Formula | Description |
|--------|---------|-------------|
| Parse Rate | 유효 JSON / 전체 | 출력 파싱 성공률 |
| Type Accuracy | 정확 type / 전체 | Action type 일치율 |
| Bounds IoU | IoU(GT, Pred) | Bounding box 겹침 비율 |
| Params Accuracy | 정확 params / 전체 | Action params 일치율 |
| **Overall Score** | Type × (0.5×IoU + 0.5×Params) | **winner 선택 지표** |

비교: Base (Zero-shot) vs stage2 vs stage1+stage2

---

## 4. 데이터셋

### 4.1 출처 및 규모

모바일 UI 인터랙션 데이터로부터 구성. 각 샘플은 스크린샷(PNG) + UI 계층구조(XML) + 액션 정보를 포함.

`gui-model.ipynb` Cell 3 에서 두 데이터셋 모두 자동 설정 (`CONFIGS` 딕셔너리):

| 데이터셋 | Stage 1 | Stage 2 | Images | 총 크기 | 용도 |
|----------|---------|---------|--------|---------|------|
| **MobiBench** | 3,145 건 | 3,147 건 | 3,655 개 | ~28 MB | 소규모 실험, 빠른 반복 |
| **AndroidControl** (주력) | 71,047 건 | 91,677 건 | 20,129 개 | ~479 MB | 대규모 학습, 본 실험 |

### 4.2 Stage 1 (World Modeling)

| 항목 | MobiBench | AndroidControl |
|------|-----------|----------------|
| 원본 데이터 | gui-model_stage1.jsonl (3,145건) | gui-model_stage1.jsonl (71,047건) |
| Train Split | ~2,987 건 (95%) | 67,494 건 (95%) |
| Test Split | ~158 건 (5%) | 3,553 건 (5%) |
| Split Method | Random, seed=42 | Random, seed=42 |
| Format | ShareGPT (multimodal) | ShareGPT (multimodal) |
| Task | UI State (XML) + Action → Next UI State (XML) | 동일 |

### 4.3 Stage 2 (Action Prediction)

| 항목 | MobiBench | AndroidControl |
|------|-----------|----------------|
| 원본 데이터 | gui-model_stage2.jsonl (3,147건) | gui-model_stage2.jsonl (91,677건) |
| Train Split | ~2,987 건 (95%) | 87,090 건 (95%) |
| Test Split | ~160 건 (5%) | 4,587 건 (5%) |
| Split Method | Stratified by action type, seed=42 | Stratified by action type, seed=42 |
| Task | Screenshot + UI State + Task → Action (JSON) | 동일 |

### 4.4 이미지

| 데이터셋 | 이미지 수 | 경로 패턴 |
|----------|----------|----------|
| MobiBench | 3,655 개 | `MobiBench/images/episode_{id:06d}_step_{idx:04d}.png` |
| AndroidControl | 20,129 개 (추출 필요: `scripts/extract_androidcontrol_images.py`) | `AndroidControl/images/episode_{id:06d}_step_{idx:04d}.png` |

- Stage 1/2 공유
- `image_max_pixels`: 4,233,600
- LlamaFactory 데이터 디렉토리에 **symlink 로 참조** (파일 복사 없음)

### 4.5 데이터 포맷

#### ShareGPT Multimodal Format

```json
{
  "messages": [
    {"from": "system", "value": "System prompt (역할 정의)"},
    {"from": "human", "value": "<image>\n[UI XML]\n[Action JSON / Task]"},
    {"from": "gpt", "value": "[Target XML 또는 Action JSON]"}
  ],
  "images": ["path/to/screenshot.png"]
}
```

#### UI 계층구조 (XML)

```xml
<div index="0">
  <p id="title" index="1">Screen Title</p>
  <button id="action_id" description="Button label"
          long-clickable="true" index="2"/>
  <input type="text" index="3">Text input</input>
</div>
```

요소 속성: `index`(DOM 순서), `id`(고유 식별자), `description`(접근성 라벨), `clickable`/`long-clickable`(인터랙션 가능 여부)

#### Action JSON Format

```json
{
  "type": "click",
  "params": {},
  "default": true,
  "index": 23,
  "bounds": {"left": 100, "top": 200, "width": 50, "height": 50}
}
```

지원 action type: `click`, `input`, `swipe`, `long_click`, `openapp`

---

## 5. 하드웨어 및 학습 설정

### 5.1 인프라

| 항목 | 값 |
|------|-----|
| GPU | NVIDIA H100 80GB × 4 |
| 분산 학습 | torchrun (NPROC_PER_NODE=4) |
| 메모리 최적화 | DeepSpeed ZeRO Stage 3 (Stage 1) / ZeRO Stage 2 (Stage 2) |
| 정밀도 | bf16 (bfloat16) |
| Gradient Checkpointing | Enabled |
| Framework | LLaMA-Factory |
| Inference | vLLM (≥0.8.2) |

### 5.2 Stage 1 하이퍼파라미터 (Full FT)

| Parameter | MobiBench | AndroidControl |
|-----------|-----------|----------------|
| per_device_train_batch_size | 2 | 2 |
| gradient_accumulation_steps | 8 | 8 |
| effective batch | 64 (2×8×4GPU) | 64 (2×8×4GPU) |
| learning_rate | 1.0e-5 | 1.0e-5 |
| lr_scheduler_type | cosine | cosine |
| warmup_ratio | 0.05 | 0.03 |
| num_train_epochs | 5 | 3 |
| weight_decay | 0.01 | 0.01 |
| max_grad_norm | 1.0 | 1.0 |
| save_strategy | epoch | epoch |
| save_total_limit | 5 | 5 |
| 학습 중 eval | 비활성화 | 비활성화 |

### 5.3 Stage 2 하이퍼파라미터 (LoRA)

| Parameter | MobiBench | AndroidControl |
|-----------|-----------|----------------|
| per_device_train_batch_size | 2 | 2 |
| gradient_accumulation_steps | 4 | 8 |
| effective batch | 32 (2×4×4GPU) | 64 (2×8×4GPU) |
| learning_rate | 3.0e-5 | 5.0e-5 |
| lr_scheduler_type | cosine | cosine |
| warmup_ratio | 0.05 | 0.03 |
| num_train_epochs | 5 | 3 |
| weight_decay | 0.01 | 0.01 |
| max_grad_norm | 1.0 | 1.0 |
| save_strategy | epoch | epoch |
| save_total_limit | 5 | 5 |
| 학습 중 eval | 비활성화 | 비활성화 |
| LoRA r / α / dropout | 16 / 32 / 0.1 | 32 / 64 / 0.1 |

### 5.4 Best Epoch 자동 선택 파이프라인

학습 YAML 은 eval 이 비활성화되어 순수 학습만 수행한다. Best checkpoint 는 학습 완료 후 별도의 sweep 으로 선정된다:

- **Stage 1** (Hungarian F1): `stage1_eval.sh` 가 각 `checkpoint-*` 에 대해 vllm_infer 로 생성을 수행하고 `_hungarian_eval.py` 로 Hungarian F1 을 계산. 결과는 `saves/{DS}/stage1_full/full_world_model/BEST_CHECKPOINT` 에 plain text 로 기록된다. `stage1_merge.sh` (또는 notebook 의 Section 4 Merge cell) 이 이를 읽어 HF Hub + `outputs/{DS}/stage1_merged/` 양쪽에 merge 한다.
- **Stage 2** (Overall Score): `stage2_eval.sh` 가 `lora_base` / `lora_world_model` 각각 checkpoint sweep 을 수행하고 (Base: Qwen 또는 `outputs/{DS}/stage1_merged`, Adapter: `checkpoint-*/`), `_action_eval.py` 로 `overall_score` 를 계산하여 각 variant 의 `saves/{DS}/stage2_lora/{lora_base,lora_world_model}/BEST_CHECKPOINT` 에 기록한다. `stage2_merge.sh` 가 각 winner adapter 로 merge → HF Hub push + `outputs/{DS}/stage2_merged/{base,world_model}/` 로컬 복사를 수행한다.
- Merge 스크립트/cell 은 `BEST_CHECKPOINT` 파일이 없으면 **hard-fail** (fallback 없음) — eval 누락을 조기에 감지한다.
- Shell 파이프라인은 로컬 경로만 사용해 HF 의존을 제거 (`stage2_merge` 이전에도 `stage2_eval` 실행 가능).

---

## 6. 설정 파일

### 6.1 Stage 1 학습: `examples/custom/GUI-Model-{MB|AC}/stage1_full/qwen3_vl_8b_gui.yaml`

> `gui-model.ipynb` Section 0 의 **Stage 1 Training YAML 셀** 이 `CONFIGS[ds_name]["stage1"]` 기반으로 동적 생성. 아래는 MobiBench 예시이며 AndroidControl 은 데이터셋 접두사/출력 경로/하이퍼파라미터가 5.2 표와 같이 분기된다.

```yaml
### model
model_name_or_path: Qwen/Qwen3-VL-8B-Instruct
trust_remote_code: true
image_max_pixels: 4233600

### method
stage: sft
do_train: true
finetuning_type: full
freeze_vision_tower: true

### dataset
dataset: GUI-Model-MB_stage1_train
template: qwen3_vl_nothink
cutoff_len: 16384
overwrite_cache: false
preprocessing_num_workers: 16

### output
output_dir: ./saves/MB/stage1_full/full_world_model
logging_steps: 1
save_strategy: epoch
save_total_limit: 5
plot_loss: true
overwrite_output_dir: true

### train
per_device_train_batch_size: 2
gradient_accumulation_steps: 8
learning_rate: 1.0e-5
num_train_epochs: 5             # AC: 3
lr_scheduler_type: cosine
warmup_ratio: 0.05              # AC: 0.03
weight_decay: 0.01
max_grad_norm: 1.0
bf16: true
gradient_checkpointing: true
deepspeed: examples/deepspeed/ds_z3_config.json
ddp_timeout: 18000000

# 학습 중 eval 비활성화 — post-training `stage1_eval.sh` (Hungarian F1 sweep) 에서 winner 선택
```

### 6.2 Stage 1 평가 (eval_loss): `stage1_eval/base/eval_loss.yaml`

```yaml
### model
model_name_or_path: ./saves/MB/stage1_full/full_world_model
trust_remote_code: true
image_max_pixels: 4233600

### method
stage: sft
do_eval: true
finetuning_type: full
freeze_vision_tower: true

### dataset
eval_dataset: GUI-Model-MB_stage1_test
template: qwen3_vl_nothink
cutoff_len: 8192

### output
output_dir: ./saves/MB/stage1_eval/eval_loss/full_world_model
overwrite_output_dir: true

### eval
per_device_eval_batch_size: 1
```

### 6.3 Stage 1 생성 평가 (Hungarian)

Hungarian 평가는 `scripts/vllm_infer.py` 가 생성을 수행하고, `scripts/_hungarian_eval.py score` 가 checkpoint 별 `metrics.json` 을 작성하며, `_hungarian_eval.py select` 가 winner 를 `BEST_CHECKPOINT` 에 기록한다. 수동 디버깅 시 legacy YAML (`stage1_eval/predict.yaml`) 로 LLaMA-Factory predict 를 직접 호출하는 것도 가능하다.

### 6.4 Stage 2 학습 (LoRA): `examples/custom/GUI-Model-{MB|AC}/stage2_lora/{base,world_model}.yaml`

> `gui-model.ipynb` Section 0 의 **Stage 2 Training YAML 셀** 이 데이터셋별로 `base.yaml` 과 `world_model.yaml` 두 개를 자동 생성. 두 파일은 `model_name_or_path` 와 `output_dir` 만 다르다.

| Exp | model_name_or_path | output_dir |
|-----|--------------------|------------|
| stage1+stage2 (`world_model.yaml`) | `./outputs/{MB\|AC}/stage1_merged` (로컬 merged) | `./saves/{MB\|AC}/stage2_lora/lora_world_model` |
| stage2 (`base.yaml`) | `Qwen/Qwen3-VL-8B-Instruct` | `./saves/{MB\|AC}/stage2_lora/lora_base` |

```yaml
### method
stage: sft
do_train: true
finetuning_type: lora
freeze_vision_tower: true
lora_rank: 16                            # AC: 32
lora_alpha: 32                           # AC: 64
lora_target: all
lora_dropout: 0.1

### dataset
dataset: GUI-Model-MB_stage2_train       # AC: GUI-Model-AC_stage2_train
template: qwen3_vl_nothink
cutoff_len: 16384
overwrite_cache: false
preprocessing_num_workers: 16

### output
save_strategy: epoch
save_total_limit: 5

### train
per_device_train_batch_size: 2
gradient_accumulation_steps: 4           # AC: 8
learning_rate: 3.0e-5                    # AC: 5.0e-5
num_train_epochs: 5                      # AC: 3
lr_scheduler_type: cosine
warmup_ratio: 0.05                       # AC: 0.03
weight_decay: 0.01
max_grad_norm: 1.0
bf16: true
gradient_checkpointing: true
ddp_timeout: 18000000

# 학습 중 eval 비활성화 — post-training `stage2_eval.sh` (Overall Score sweep) 에서 variant 별 winner 선택
```

### 6.5 데이터 Split 및 등록

**사전 준비**: `scripts/split_data.py` 로 Train/Test Split 파일을 생성한다.

```bash
python scripts/split_data.py --dataset MobiBench       # data/MobiBench/ 내에 _train/_test 생성
python scripts/split_data.py --dataset AndroidControl   # data/AndroidControl/ 내에 _train/_test 생성
```

- Stage 1: Random split (seed=42, 95:5)
- Stage 2: Stratified split by action type (seed=42, 95:5)

**등록**: notebook Section 1-2 의 데이터 등록 셀 실행 시 `LlamaFactory/data/dataset_info.json` 에 상대 경로로 자동 등록:

```json
{
  "GUI-Model-MB_stage1_train": {
    "file_name": "../../data/MobiBench/gui-model_stage1_train.jsonl"
  }
}
```

JSONL 파일을 LlamaFactory/data/ 로 복사하지 않는다. 이미지 디렉토리만 symlink 로 생성.

**데이터셋 키**:
- MobiBench: `GUI-Model-MB_stage{1,2}_{train,test}`
- AndroidControl: `GUI-Model-AC_stage{1,2}_{train,test}`

YAML 설정 파일 위치: `LlamaFactory/examples/custom/GUI-Model-{MB,AC}/`

---

## 7. CLI 인터페이스

전체 파이프라인은 `gui-model.ipynb` 셀 실행이 표준이지만, Section 0 (환경 설정 + Training/Eval YAML 선행 생성) 과 데이터 등록 셀을 한 번 실행하면 이후 학습/평가/Merge 단계는 `scripts/` 쉘 스크립트로 반복 실행할 수 있다. Merge YAML 은 Section 5/8 내부 셀에서 `BEST_CHECKPOINT` 를 읽어 자동 생성되므로 노트북 재실행 시 별도 수동 조작 불필요.

### 7.1 쉘 스크립트 (권장, 재실행·자동화)

| 스크립트 | 대응 Section | 수행 |
|---|---|---|
| `scripts/setup.sh` | Section 0 Cell 4/5 | 초기 환경 설치 (requirements.txt + LlamaFactory clone + metrics/deepspeed/vllm). idempotent (LlamaFactory 존재 시 clone skip) |
| `scripts/stage1_train.sh` | Section 3 | Stage 1 Full FT (FORCE_TORCHRUN=1, H100×4) → `saves/{DS}/stage1_full/full_world_model/` |
| `scripts/stage1_eval.sh`  | Section 4 | **Phase A** Baseline(Zero-shot) Hungarian → **Phase B** 전체 checkpoint sweep (vllm_infer + `_hungarian_eval.py score`) → **Phase C** winner 선택 (`_hungarian_eval.py select`) → `saves/{DS}/.../BEST_CHECKPOINT` 기록 |
| `scripts/stage1_merge.sh` | Section 5 | **BEST_CHECKPOINT 필수(없으면 hard-fail)**. Merge YAML 을 winner 경로로 override → `llamafactory-cli export` (HF Hub push) → `exports/.../` 를 `outputs/{DS}/stage1_merged/` 로 rsync |
| `scripts/stage2_train.sh` | Section 6 | Stage 2 LoRA (base, world_model) → `saves/{DS}/stage2_lora/{lora_base,lora_world_model}/` |
| `scripts/stage2_eval.sh`  | Section 7 | **Phase A** Baseline(Zero-shot) → **Phase B** `lora_base`/`lora_world_model` 각각 체크포인트 sweep (`--adapter_name_or_path`) + `_action_eval.py score` → **Phase C** winner 선택 (`select`, 기본 지표 `overall_score`) → 각 lora 디렉토리에 `BEST_CHECKPOINT` 기록. **완전 로컬 — HF Hub 의존 없음** |
| `scripts/stage2_merge.sh` | Section 8 | **각 variant BEST_CHECKPOINT 필수(없으면 hard-fail)**. 각 variant Merge YAML 을 winner adapter 경로로 override → `llamafactory-cli export` (HF Hub push) → `outputs/{DS}/stage2_merged/{base,world_model}/` 로 rsync. `lora_world_model` 의 base 는 `outputs/{DS}/stage1_merged` (로컬) |

**공통 인자**: `MB | AC | all` (기본 `all`). 로그: `logs/<script>_<DS>_<ts>.log` 로 tee. `set -euo pipefail` 로 단계 실패 시 즉시 중단.
**전제**: bash 4+ (macOS 기본 3.2 → `brew install bash`), LLaMA-Factory 설치, `.env` 의 `HF_TOKEN` (merge 스크립트 전용).

```bash
# 데이터 Split (학습 전 1회)
python scripts/split_data.py --dataset MobiBench
python scripts/split_data.py --dataset AndroidControl

# 전체 파이프라인 (6-스크립트 순서)
./scripts/stage1_train.sh     # 1. checkpoint-* 생성
./scripts/stage1_eval.sh      # 2. Baseline + sweep + Hungarian F1 winner 선택 → BEST_CHECKPOINT
./scripts/stage1_merge.sh     # 3. winner merge → HF + local
./scripts/stage2_train.sh     # 4. Stage 2 LoRA (stage1_merged base)
./scripts/stage2_eval.sh      # 5. 3-Way sweep + Overall Score winner 선택
./scripts/stage2_merge.sh     # 6. Stage 2 winner merge & push

# 단일 데이터셋
./scripts/stage1_train.sh MB
./scripts/stage2_eval.sh AC
```

### 7.2 수동 llamafactory-cli (디버깅 · 단일 셀 재현)

```bash
# Stage 1: World Modeling Full FT (H100 80GB × 4)
cd LlamaFactory
FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=4 \
  llamafactory-cli train examples/custom/GUI-Model-MB/stage1_full/qwen3_vl_8b_gui.yaml

# Stage 1 평가: eval_loss
llamafactory-cli train examples/custom/GUI-Model-MB/stage1_eval/base/eval_loss.yaml

# Stage 1 평가: Hungarian Matching (vllm_infer.py 기반 생성 + 자체 메트릭 함수)
python scripts/vllm_infer.py --model_name_or_path <model> --dataset <ds_test> \
  --template qwen3_vl_nothink --cutoff_len 8192 --image_max_pixels 4233600 --enable_thinking False \
  --save_name <out>/generated_predictions.jsonl --matrix_save_name <out>/predict_results.json

# Stage 2: Action Prediction LoRA FT (노트북 Section 6 Stage 2 SFT Training 셀에는 torchrun prefix 없음)
llamafactory-cli train examples/custom/GUI-Model-MB/stage2_lora/<base|world_model>.yaml

# LoRA 어댑터 → Base Model 병합 (Stage 2)
llamafactory-cli export examples/merge_custom/GUI-Model-MB/stage2/<merge_base|merge_world_model>.yaml

# Stage 1 Merge (Full FT → HF Hub push)
llamafactory-cli export examples/merge_custom/GUI-Model-MB/gui/qwen3_vl_8b_gui.yaml

# HuggingFace Hub 업로드는 merge YAML 의 export_hub_model_id 필드로 자동 처리
```

---

## 8. 의존성

### 8.1 시스템

```
- Python >= 3.10
- CUDA >= 12.1
- NVIDIA GPU (H100 80GB × 4)
- OS: Linux (Ubuntu 22.04+)
```

### 8.2 Core

| 패키지 | 버전 | 용도 |
|--------|------|------|
| torch | ≥2.4.0 | 딥러닝 프레임워크 |
| torchvision | ≥0.19.0 | 이미지 처리 |
| transformers | ≥5.0.0 | VLM 모델 로드 및 학습 |
| peft | ≥0.18.0, ≤0.18.1 | LoRA 구현 |
| accelerate | ≥1.3.0, ≤1.11.0 | 분산 학습 |
| trl | ≥0.18.0, ≤0.24.0 | 강화 학습 (확장용) |
| datasets | ≥2.16.0, ≤4.0.0 | 데이터 로드 |
| deepspeed | — | ZeRO 메모리 최적화 |
| vllm | ≥0.8.2 | 고속 추론 |

### 8.3 Evaluation

| 패키지 | 용도 |
|--------|------|
| beautifulsoup4 | XML 파싱 (Hungarian Matching) |
| munkres | 헝가리안 알고리즘 |
| nltk | BLEU-4 계산 |
| rouge | ROUGE-L 계산 |
| jieba | 중국어 토크나이징 |

### 8.4 Infrastructure

| 패키지 | 용도 |
|--------|------|
| pillow | 이미지 처리 |
| gradio | Web UI (선택) |
| flash-attn | Flash Attention (선택) |

---

## 9. 프로젝트 디렉토리 구조

```
GUI-Model/
├── ARCHITECTURE.md                 # 본 문서 (시스템 아키텍처 & 파이프라인)
├── CLAUDE.md                       # Claude Code 컨텍스트
├── README.md                       # 프로젝트 개요 및 실행 가이드
├── gui-model.ipynb                 # 전체 파이프라인 실행 노트북
├── requirements.txt                # Python 의존성
├── .env.example                    # 환경변수 템플릿
│
├── data/                           # 데이터셋 (git-ignored)
│   ├── MobiBench/
│   │   ├── images/                 # 모바일 UI 스크린샷 (3,655개 PNG)
│   │   ├── gui-model_stage1.jsonl  # Stage 1 (3,145건)
│   │   └── gui-model_stage2.jsonl  # Stage 2 (3,147건)
│   └── AndroidControl/
│       ├── images/                 # 스크린샷 (20,129개)
│       ├── gui-model_stage1.jsonl  # Stage 1 (71,047건)
│       └── gui-model_stage2.jsonl  # Stage 2 (91,677건)
│
├── scripts/                        # 실행 & 유틸리티 스크립트
│   ├── split_data.py               # Train/Test Split CLI
│   ├── extract_androidcontrol_images.py
│   ├── _common.sh                  # 쉘 스크립트 공통 헬퍼 (경로/로깅/인자 파싱)
│   ├── _hungarian_eval.py          # Stage 1 체크포인트별 Hungarian/BLEU/ROUGE + winner (score/select)
│   ├── _action_eval.py             # Stage 2 체크포인트별 Action 메트릭 + winner (score/select)
│   ├── stage1_train.sh             # Stage 1 Full FT
│   ├── stage1_eval.sh              # Stage 1 평가 — Hungarian F1 winner 선택
│   ├── stage1_merge.sh             # Stage 1 Merge — BEST_CHECKPOINT 기반 HF push + 로컬 복사
│   ├── stage2_train.sh             # Stage 2 LoRA (base / world_model)
│   ├── stage2_eval.sh              # Stage 2 3-Way 평가 — variant 별 Overall Score winner 선택
│   └── stage2_merge.sh             # Stage 2 Merge — winner adapter merge + HF push
│
├── logs/                           # 쉘 스크립트 실행 로그 (git-ignored, tee 자동 저장)
│
├── LlamaFactory/                   # LLaMA-Factory 프레임워크
│   ├── src/llamafactory/
│   ├── examples/
│   │   └── custom/GUI-Model-{MB,AC}/   # 학습/평가 YAML 설정 파일
│   │       ├── stage1_full/            # Stage 1 Full FT 설정
│   │       ├── stage1_eval/            # Stage 1 평가 설정 (eval_loss, predict)
│   │       ├── stage2_lora/            # Stage 2 LoRA 설정 (base, world_model)
│   │       └── stage2_eval/            # Stage 2 평가 설정
│   ├── saves/                      # 학습 중간 결과 (training checkpoints + post-training eval)
│   │   ├── {DS}/stage1_full/full_world_model/
│   │   │   ├── checkpoint-*                # epoch 별 저장
│   │   │   ├── BEST_CHECKPOINT             # Hungarian F1 winner 이름 (plain text)
│   │   │   └── BEST_CHECKPOINT.json        # winner 선정 상세
│   │   ├── {DS}/stage1_eval/
│   │   │   ├── eval_loss/                  # Loss 메트릭
│   │   │   └── hungarian_matching/         # 체크포인트별 Hungarian 스코어
│   │   ├── {DS}/stage2_lora/
│   │   │   ├── lora_base/                  # checkpoint-* + BEST_CHECKPOINT
│   │   │   └── lora_world_model/           # checkpoint-* + BEST_CHECKPOINT
│   │   └── {DS}/stage2_eval/               # Stage 2 3-Way 평가 중간 결과
│   │       ├── base/                       # Baseline (Zero-shot)
│   │       ├── lora_base/                  # stage2 (Control)
│   │       └── lora_world_model/           # stage1+stage2 (World Model)
│   ├── outputs/                    # Merged 모델 artifact 전용 (llamafactory export 결과)
│   │   ├── {DS}/stage1_merged/             # Stage 1 winner merge 로컬 복사
│   │   └── {DS}/stage2_merged/
│   │       ├── base/                       # Stage 2 merge_base winner 복사
│   │       └── world_model/                # Stage 2 merge_world_model winner 복사
│   └── data/                       # 데이터 설정 템플릿
│       ├── dataset_info.json               # 상대 경로 참조 (../../data/{DATASET_NAME}/...)
│       ├── GUI-Model-MB/                   # 이미지 symlink
│       └── GUI-Model-AC/                   # 이미지 symlink
│
└── .claude/                        # Claude Code 프로젝트 파일
    ├── plans/                      # 개발 계획 문서
    └── reference/
        └── metrics/                # 커스텀 평가 메트릭 구현
            ├── metric.py                   # BLEU, ROUGE, Hungarian 통합
            ├── hungarian_metric.py         # 헝가리안 매칭 알고리즘
            └── patch_guide_0315.txt        # LLaMA-Factory 메트릭 패치 가이드
```
