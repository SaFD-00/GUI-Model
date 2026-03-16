# PRD: Monkey

> **Version**: 0.1.0
> **Last Updated**: 2026-03-16
> **Status**: Draft
> **Author**: bsw
> **Base Model**: [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)
> **Upstream**: [UI-TARS](https://github.com/bytedance/UI-TARS), [OS-Atlas](https://github.com/OS-Copilot/OS-Atlas), [ShowUI](https://github.com/showlab/ShowUI), [GUI-Libra](https://arxiv.org/abs/2502.xxxxx), [MobileRL](https://arxiv.org/html/2509.18119)

---

## 1. 개요

### 1.1 프로젝트 요약

**Monkey**는 Qwen3-VL-8B-Instruct를 기반으로 curriculum learning 방식을 적용하여 범용 GUI Foundation Model을 구축하는 학습 파이프라인이다.

GUI 스크린샷을 입력으로 받아 UI 요소를 인식(perception)하고, 사용자의 자연어 지시를 따라 정확한 GUI 조작 액션을 생성(action)하는 모델을 목표로 한다.

### 1.2 핵심 가치

| 기존 방식 | 문제 | Monkey |
|-----------|------|--------|
| API 기반 GUI Agent (GPT-4V, Claude) | 높은 비용, 느린 추론 속도, 외부 API 의존 | 로컬 추론 가능한 8B 경량 모델 |
| 단일 태스크 SFT 모델 | GUI 이해 없이 행동만 학습, 일반화 실패 | World Modeling → Task SFT → RL 단계적 학습 |
| Grounding-only 모델 (SeeClick 등) | 요소 위치만 파악, 실제 태스크 수행 불가 | 인식부터 장기 태스크 수행까지 end-to-end |

### 1.3 Base Model: Qwen3-VL-8B-Instruct

| 항목 | 상세 |
|------|------|
| 아키텍처 | ViT visual encoder + Qwen LLM (Dense, 8B params) |
| 핵심 기술 | Interleaved-MRoPE (위치 인코딩), DeepStack (시각-텍스트 정렬) |
| 컨텍스트 | 256K tokens (최대 1M tokens 확장 가능) |
| GUI 지원 | 네이티브 GUI 에이전트 기능 — 요소 인식, 기능 이해, 도구 호출 |
| 시각 능력 | 32언어 OCR, 2D grounding, 동적 해상도, 멀티이미지/비디오 |
| 선정 이유 | 8B 규모에서 최고 수준의 GUI 이해력, 오픈소스, 활발한 생태계 |

### 1.4 기술적 차별점

- **Curriculum Learning**: GUI World Modeling으로 시각적 기반 이해를 먼저 확립한 후, 점진적으로 태스크 복잡도를 높여가는 단계적 학습 (UI-TARS, MobileRL 접근 참고)
- **Phase 1 SFT + Phase 2 RL 이중 구조**: SFT로 행동 분포의 기초를 잡고, RL(GRPO)로 태스크 완료 성공률을 극대화
- **Full FT → LoRA 효율적 전환**: Stage 1에서 visual encoder + LLM 전체를 fine-tuning하여 GUI 도메인에 적응시킨 후, Stage 2부터 LoRA로 효율적 태스크 학습
- **Short-horizon + Long-horizon 통합**: 단일 액션(클릭, 타이핑)부터 다단계 workflow까지 하나의 모델로 처리
- **Pure Screenshot 기반**: DOM/a11y tree 없이 스크린샷만으로 동작하는 vision-only 접근 (UI-TARS 방식)

---

## 2. 시스템 아키텍처

### 2.1 계층 구조

```
┌─────────────────────────────────────────────────────────────────┐
│                         Monkey Pipeline                         │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    Phase 1: SFT                           │  │
│  │                                                           │  │
│  │  ┌─────────────────┐       ┌───────────────────────────┐  │  │
│  │  │   Stage 1        │       │   Stage 2                 │  │  │
│  │  │   GUI World      │──────►│   Task Finetuning         │  │  │
│  │  │   Modeling        │       │   (LoRA)                  │  │  │
│  │  │   (Full FT)      │       │                           │  │  │
│  │  └─────────────────┘       └───────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    Phase 2: RL                             │  │
│  │                                                           │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │   Task RL Finetuning (GRPO)                         │  │  │
│  │  │   Short-horizon + Long-horizon                      │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  Data    │  │  Model   │  │  Eval    │  │  Inference    │  │
│  │  Module  │  │  Module  │  │  Module  │  │  Module       │  │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 데이터 흐름

```
[Raw GUI Datasets]
    │
    ▼ Data Processing
[Unified Dataset Format] → 스크린샷 + 액션 시퀀스 + 메타데이터
    │
    ├──► Stage 1: GUI World Modeling Data
    │    (screenshot, question, answer) 형태
    │    - 요소 grounding, OCR, 레이아웃 이해, 상태 예측
    │
    ├──► Stage 2: Task SFT Data
    │    (screenshot, instruction, action_sequence) 형태
    │    - Short-horizon: 단일/소수 스텝 액션
    │    - Long-horizon: 다단계 태스크 trajectory
    │
    └──► Phase 2: RL Environment
         (screenshot, instruction) → model action → reward
         - 환경 실행 또는 규칙 기반 reward
```

### 2.3 핵심 컴포넌트

| 컴포넌트 | 역할 | 기술 스택 |
|----------|------|----------|
| Data Pipeline | 데이터셋 수집, 전처리, 통합 포맷 변환 | Python, datasets, PIL |
| Model Wrapper | Qwen3-VL 모델 로딩, LoRA 적용, 체크포인트 관리 | transformers, PEFT |
| SFT Trainer | Stage 1 Full FT, Stage 2 LoRA 학습 | transformers.Trainer, DeepSpeed |
| RL Trainer | GRPO 기반 강화학습 | trl, OpenRLHF |
| Eval Engine | 벤치마크 평가 및 메트릭 산출 | vLLM, custom evaluators |
| Inference Server | 학습 완료 모델 서빙 | vLLM, SGLang |

---

## 3. 액션 스페이스

### 3.1 기본 액션

| 액션 | 파라미터 | 설명 |
|------|---------|------|
| Click | coordinate: [x, y] | 화면 좌표 탭 |
| Type | text: str | 텍스트 입력 |
| Scroll | direction: up\|down\|left\|right | 스크롤 |
| Back | - | 뒤로가기 |
| Home | - | 홈 화면 |
| Wait | duration: float | 대기 |

### 3.2 좌표 체계

- 절대 픽셀 좌표 (해상도별 상이)
- 정규화 좌표 [0, 1000] 범위 (학습 시 사용)

---

## 4. 데이터 수집 파이프라인

### 4.1 수집 아키텍처

```
Server/ (Python)                AVD
┌────────────────────┐         ┌──────────────────────────┐
│ Smart Monkey       │──ADB──▶│ Target App               │
│ (XML→액션 선택→    │ input   │ ← adb input tap/swipe    │
│  ADB 실행)         │         │                          │
│ TCP Server         │◀──TCP──│ App/ (AccessibilityService)│
│ (데이터 수신 +     │         │ • UI 변화 감지 → 캡처     │
│  XML 동기화)       │         └──────────────────────────┘
│ Annotation         │
└────────────────────┘
```

- **Smart Monkey**: UIAutomator XML 파싱 → 지능형 액션 선택 (tap/swipe/input_text/back/home/long_press) → ADB로 실행. MobileForge에서 포팅
- **TCP Server**: Android App에서 스크린샷+XML 데이터 수신 + Smart Monkey 동기화 (`wait_for_xml()`)
- **App (Kotlin)**: AccessibilityService로 UI 변화 감지, 화면 안정화 후 스크린샷+XML 캡처 및 TCP 전송
- **Annotation Pipeline**: 수집 후 오프라인으로 raw data → 학습 포맷 변환

### 4.2 수집 프로토콜

1. Server → Smart Monkey가 앱 실행 후 초기 XML 획득
2. Smart Monkey → XML 파싱 → clickable/editable/scrollable 요소 식별 → 가중치 기반 액션 선택
3. Server → `adb input tap/swipe/text`로 정확한 UI 요소에 이벤트 실행
4. App(AccessibilityService) → UI 변화 감지 → **화면 안정화 대기** → 스크린샷 + XML + package 정보를 TCP로 Server 전송
5. Server → 수신 XML로 다음 Smart Monkey 액션 결정 (step-by-step 동기화 루프)
6. App → top_package != target_package 감지 시 → Server에 'E' 알림 + back 실행
7. 수집 완료 후 → Annotation Pipeline으로 raw data → Stage 1 학습 포맷 변환

### 4.2.0 Smart Monkey 액션 가중치

| 액션 | 기본 가중치 | 동적 조정 |
|------|-----------|----------|
| tap | 60% | clickable 없으면 5%로 감소 |
| press_back | 10% | - |
| swipe | 10% | scrollable 없으면 2%로 감소 |
| input_text | 10% | EditText 있으면 25%로 증가 |
| long_press | 5% | - |
| press_home | 5% | - |

### 4.2.1 Visual Screen Stabilization

캡처 품질 향상을 위해 AccessibilityEvent 트리거 후 실제 캡처 전에 비주얼 안정화 레이어를 적용한다.
[computer-use-preview-for-mobile](https://github.com/nicholasgcoles/computer-use-preview-for-mobile) 참조 구현을 기반으로 포팅.

- **MediaProjection 저해상도 캡처**: 100px 너비로 프레임 캡처 (비교 전용)
- **BitmapComparator**: 연속 프레임 간 픽셀 차이 비교 (RGBA 32-bit)
- **안정화 조건**: 3연속 프레임이 2% 이내 차이 → 안정 판정 (500ms 간격)
- **시각적 변화 확인**: 이전 안정 프레임과 현재 프레임 비교로 중복 캡처 방지
- **Timeout**: 최대 15초 (30회 시도), 초과 시 캡처 계속 진행

**TCP 메시지 프로토콜**:

| 타입 | 코드 | 페이로드 |
|------|------|---------|
| Screenshot | `S` | size + `\n` + JPEG bytes |
| XML | `X` | top_pkg + `\n` + target_pkg + `\n` + size + `\n` + XML bytes |
| External App | `E` | JSON + `\n` |
| Finish | `F` | (없음) |

### 4.3 Fallback 메커니즘

- **App 측**: top_package != target_package → `GLOBAL_ACTION_BACK` 실행 (최대 3회), 실패 시 `am start` 강제 실행
- **Server 측**: 'E' 메시지 수신 → 해당 step에 `external_app` 플래그 → annotation 시 제외

### 4.4 Annotation Pipeline

| 모듈 | 입력 | 출력 task_type |
|------|------|---------------|
| xml_parser | raw XML → UIElement 리스트 | (중간 결과) |
| xml_encoder | raw XML → HTML-style XML | (중간 결과) |
| grounding | UIElement (clickable/visible) | grounding |
| ocr_extractor | UIElement (text 있는 것) | ocr |
| state_diff | 연속 step의 XML 쌍 비교 | state_diff |
| element_qa | UIElement 속성 | element_qa |
| world_modeling | before/after HTML-XML + action | world_model |
| llm_annotator | 스크린샷 → LLM API | caption (선택적) |

### 4.5 수집 CLI

```bash
# 단일 앱 수집
monkey-collect run --app com.android.calculator2 --events 100

# 배치 수집 (apps.yaml 기반)
monkey-collect batch --config configs/collection/apps.yaml

# annotation 실행
monkey-collect annotate --session <session_id>

# 전체 (수집 + annotation)
monkey-collect pipeline --config configs/collection/apps.yaml
```

### 4.6 설정 스키마

수집 설정: `configs/collection/default.yaml`
대상 앱 목록: `configs/collection/apps.yaml`

---

## 5. 파이프라인/오케스트레이터

### 5.1 파이프라인 단계

```
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

### 5.2 오케스트레이터

```
클래스: TrainingPipeline

메서드:
  run_stage1(config: Stage1Config) → CheckpointPath
    GUI World Modeling 풀 파인튜닝 실행
    - 모델 전체 파라미터 업데이트
    - 멀티 태스크 학습 (grounding + OCR + captioning + ...)
    - 체크포인트 저장 및 중간 평가

  run_stage2(config: Stage2Config, base_ckpt: CheckpointPath) → CheckpointPath
    Stage 1 체크포인트 위에 LoRA 어댑터 학습
    - Short-horizon + Long-horizon 태스크 혼합
    - LoRA rank/alpha 설정에 따른 효율적 학습

  run_rl(config: RLConfig, base_ckpt: CheckpointPath) → CheckpointPath
    GRPO 기반 강화학습 실행
    - SFT 체크포인트를 초기 정책으로 사용
    - 환경 또는 규칙 기반 reward 함수
    - KL divergence 제약으로 catastrophic forgetting 방지

  evaluate(ckpt: CheckpointPath, benchmarks: list[str]) → EvalResults
    지정 벤치마크에서 모델 성능 평가
```

---

## 6. 모듈 상세 요구사항

### 6.1 Data Module: `data/`

#### 6.1.1 목적

다양한 GUI 데이터셋을 수집하고, 학습 단계별로 요구되는 통합 포맷으로 변환한다.

#### 6.1.2 파일 구성

```
data/
├── __init__.py
├── download.py           # 데이터셋 다운로드 스크립트
├── preprocessing.py      # 이미지 전처리 (리사이즈, 정규화)
├── format_converter.py   # 데이터셋별 → 통합 포맷 변환
├── dataset.py            # PyTorch Dataset 클래스
├── collator.py           # Data collator (배치 구성)
├── datasets/             # 데이터셋별 변환 로직
│   ├── screenspot.py     # ScreenSpot grounding 데이터
│   ├── guiact.py         # GUIAct 액션 데이터
│   ├── mind2web.py       # Mind2Web 웹 네비게이션
│   ├── aitw.py           # Android in the Wild
│   ├── gui_world.py      # GUI-World 데이터셋
│   └── custom.py         # 커스텀 데이터 로더
└── prompts/
    ├── grounding.py      # Grounding 태스크 프롬프트 템플릿
    ├── ocr.py            # OCR 태스크 프롬프트 템플릿
    ├── action.py         # 액션 예측 프롬프트 템플릿
    └── caption.py        # 스크린 캡셔닝 프롬프트 템플릿
```

#### 6.1.3 통합 데이터 포맷

**Stage 1 — World Modeling 포맷**:

```json
{
  "id": "unique_id",
  "image": "path/to/screenshot.png",
  "conversations": [
    {"role": "user", "content": "<image>\n{task_prompt}"},
    {"role": "assistant", "content": "{answer}"}
  ],
  "task_type": "grounding | ocr | caption | state_diff | element_qa",
  "metadata": {
    "source": "dataset_name",
    "platform": "web | android | ios | desktop",
    "resolution": [width, height]
  }
}
```

**Stage 2 — Task SFT 포맷**:

```json
{
  "id": "unique_id",
  "task_instruction": "사용자 지시문",
  "trajectory": [
    {
      "screenshot": "path/to/step_0.png",
      "action": {
        "type": "click | type | scroll | swipe | press | wait",
        "coordinate": [x, y],
        "text": "optional_input_text",
        "direction": "up | down | left | right"
      },
      "thought": "모델의 reasoning (optional)"
    }
  ],
  "horizon": "short | long",
  "metadata": {
    "source": "dataset_name",
    "platform": "web | android | ios | desktop",
    "num_steps": 5
  }
}
```

#### 6.1.4 데이터셋 목록

**Stage 1 — GUI World Modeling**:

| 데이터셋 | 태스크 | 플랫폼 | 규모 (예상) |
|----------|--------|--------|------------|
| OS-Atlas Grounding Corpus | Element Grounding (cross-platform) | Web, Mobile, Desktop | ~13M elements |
| ScreenSpot / ScreenSpot-Pro | Element Grounding | Web, Mobile, Desktop | ~100K |
| GUI-World | Screenshot QA / Captioning | Cross-platform | ~50K |
| Widget Caption | Element Description | Android | ~160K |
| Rico SCA | Screen Captioning | Android | ~60K |
| UGround (10M elements) | Visual Grounding with referring expressions | Cross-platform | ~1.3M screenshots |
| Web Screenshot (자체 수집) | OCR / Layout Understanding | Web | ~100K |
| State Diff Pairs (자체 생성) | State Change Detection | Cross-platform | ~50K |

**Stage 2 — Task Finetuning**:

| 데이터셋 | 태스크 | Horizon | 플랫폼 | 규모 (예상) |
|----------|--------|---------|--------|------------|
| GUIAct (Web/Smartphone) | Action Prediction | Short | Web, Android | ~70K |
| AITW (Android in the Wild) | Action Sequence | Short/Long | Android | ~715K trajectories |
| Mind2Web | Web Navigation | Long | Web | ~12K |
| OS-Atlas Action Data | Cross-platform Action | Short/Long | Cross-platform | ~200K |
| VideoGUI | Instructional Video → GUI Actions | Long | Cross-platform | ~86 tasks, 463 subtasks |
| UIPro (Unified Action Space) | Harmonized Multi-domain Actions | Short/Long | Cross-platform | 합성 데이터 |
| Custom Trajectories | Domain-specific Tasks | Long | Web | TBD |

---

### 6.2 Model Module: `model/`

#### 6.2.1 목적

Qwen3-VL-8B-Instruct 모델의 로딩, LoRA 어댑터 적용, 체크포인트 관리를 담당한다.

#### 6.2.2 파일 구성

```
model/
├── __init__.py
├── loader.py             # 모델 및 프로세서 로딩
├── lora.py               # LoRA 어댑터 설정 및 적용
├── merge.py              # LoRA 어댑터 병합
└── utils.py              # 모델 유틸리티 (파라미터 카운트 등)
```

#### 6.2.3 Pseudo-Spec

**`loader.py`**

```
함수:
  load_model(
    model_name_or_path: str = "Qwen/Qwen3-VL-8B-Instruct",
    attn_implementation: str = "flash_attention_2",
    torch_dtype: torch.dtype = torch.bfloat16,
    device_map: str = "auto"
  ) → tuple[Qwen3VLForConditionalGeneration, AutoProcessor]:
    모델과 프로세서를 로드한다.
    1. AutoProcessor.from_pretrained로 프로세서 로드
    2. Qwen3VLForConditionalGeneration.from_pretrained로 모델 로드
    3. flash_attention_2 또는 sdpa 적용
    4. gradient_checkpointing 활성화 (학습 시)
```

**`lora.py`**

```
함수:
  apply_lora(
    model: PreTrainedModel,
    r: int = 64,
    lora_alpha: int = 128,
    target_modules: list[str] = ["q_proj", "k_proj", "v_proj", "o_proj",
                                  "gate_proj", "up_proj", "down_proj"],
    lora_dropout: float = 0.05,
    modules_to_save: list[str] | None = None
  ) → PeftModel:
    LoRA 어댑터를 모델에 적용한다.
    - Stage 2 및 RL에서 사용
    - vision encoder는 freeze 상태 유지
    - LLM backbone에만 LoRA 적용
```

---

### 6.3 Training Module: `training/`

#### 6.3.1 목적

Stage별 학습 루프, 학습률 스케줄링, 분산 학습 설정을 관리한다.

#### 6.3.2 파일 구성

```
training/
├── __init__.py
├── sft_trainer.py        # SFT 학습 (Stage 1 & 2)
├── rl_trainer.py         # RL 학습 (Phase 2)
├── reward.py             # Reward 함수 정의
├── callbacks.py          # 학습 콜백 (로깅, 체크포인트)
└── utils.py              # 학습 유틸리티
```

#### 6.3.3 Pseudo-Spec

**`sft_trainer.py`**

```
클래스: GUISFTTrainer(Trainer)

  속성:
    model: PreTrainedModel
    processor: AutoProcessor
    train_dataset: Dataset
    eval_dataset: Dataset
    stage: Literal["stage1", "stage2"]

  메서드:
    compute_loss(model, inputs, return_outputs) → torch.Tensor:
      Qwen3-VL 포맷에 맞는 loss 계산
      - 이미지 토큰 처리
      - 멀티턴 대화 포맷 loss masking (user turn은 loss 제외)

Stage 1 설정:
  - 학습 대상: 전체 모델 파라미터 (Full Finetuning)
  - Optimizer: AdamW (lr=1e-5, weight_decay=0.01)
  - Scheduler: cosine with warmup (warmup_ratio=0.03)
  - Batch size: effective 128 (gradient accumulation 활용)
  - Epochs: 1~2
  - Mixed precision: bf16
  - 분산 전략: DeepSpeed ZeRO-3 또는 FSDP

Stage 2 설정:
  - 학습 대상: LoRA 어댑터만 (LLM backbone)
  - Optimizer: AdamW (lr=2e-4)
  - Scheduler: cosine with warmup (warmup_ratio=0.03)
  - Batch size: effective 64
  - Epochs: 2~3
  - Mixed precision: bf16
  - 분산 전략: DeepSpeed ZeRO-2
```

**`rl_trainer.py`**

```
클래스: GUIRLTrainer

  속성:
    model: PeftModel              # SFT 체크포인트 기반
    ref_model: PreTrainedModel    # Reference 모델 (KL 제약)
    reward_fn: RewardFunction
    tokenizer: AutoProcessor

  메서드:
    train(config: RLConfig) → None:
      GRPO (Group Relative Policy Optimization) 학습 루프
      1. 배치에서 다수의 응답 샘플링 (group_size=4~8)
      2. 각 응답에 reward 계산 (RLVR: rule-based verifiable rewards)
      3. 그룹 내 상대적 reward로 advantage 추정
      4. Policy gradient 업데이트 + KL penalty

      AdaGRPO 확장 (MobileRL 참고):
      - Shortest-path reward adjustment: 최적 경로에 bias
      - Adaptive positive replay: 성공 trajectory 강조
      - Failure curriculum filtering: 점진적 난이도 증가

RL 설정:
  - Algorithm: GRPO (AdaGRPO 변형 적용)
  - 학습 대상: LoRA 어댑터
  - KL coefficient: 0.01~0.05
  - Group size: 4~8 responses per prompt
  - Max response length: 2048 tokens
  - Optimizer: AdamW (lr=5e-6)
  - Epochs: 1~2
  - Reward: RLVR (rule-based verifiable rewards) 우선, 환경 기반 보조
```

**`reward.py`**

```
클래스: RewardFunction

  메서드:
    compute_short_horizon_reward(
      predicted_action: Action,
      ground_truth_action: Action
    ) → float:
      Short-horizon 태스크 reward 계산
      - 액션 타입 정확도: +0.3 (type match)
      - 좌표 정확도: +0.5 (IoU 또는 L2 distance threshold)
      - 텍스트 정확도: +0.2 (입력 텍스트 F1)
      - 범위: [0.0, 1.0]

    compute_long_horizon_reward(
      trajectory: list[Action],
      task_completion: bool,
      partial_progress: float
    ) → float:
      Long-horizon 태스크 reward 계산
      - 태스크 완료: +1.0
      - 부분 진행: partial_progress * 0.5
      - 스텝 효율성 보너스: -0.01 * excess_steps
      - 범위: [-0.5, 1.0]
```

---

### 6.4 Evaluation Module: `eval/`

#### 6.4.1 목적

학습된 모델의 GUI 이해 및 태스크 수행 능력을 정량적으로 평가한다.

#### 6.4.2 파일 구성

```
eval/
├── __init__.py
├── evaluator.py          # 통합 평가 엔진
├── metrics.py            # 메트릭 계산 함수
├── benchmarks/
│   ├── screenspot.py     # ScreenSpot 벤치마크
│   ├── mind2web.py       # Mind2Web 벤치마크
│   ├── aitw.py           # AITW 벤치마크
│   ├── osworld.py        # OSWorld 벤치마크
│   └── custom.py         # 커스텀 벤치마크
└── inference.py          # 배치 추론 유틸리티
```

---

### 6.5 Inference Module: `inference/`

#### 6.5.1 목적

학습 완료 모델의 서빙 및 데모를 담당한다.

#### 6.5.2 파일 구성

```
inference/
├── __init__.py
├── server.py             # vLLM/SGLang 기반 서빙
├── client.py             # 추론 클라이언트
└── demo.py               # Gradio 데모 앱
```

---

## 7. 평가/테스트

### 7.1 벤치마크 전략

| 평가 영역 | 벤치마크 | 메트릭 |
|-----------|----------|--------|
| Element Grounding | ScreenSpot | Accuracy (IoU > 0.5) |
| Element Grounding | ScreenSpot-Pro | Accuracy |
| Web Navigation | Mind2Web | Element Acc, Step SR |
| Android Tasks | AITW | Action Type Acc, Overall Acc |
| Desktop Tasks | OSWorld | Task Success Rate |
| Cross-platform | GUI-Odyssey | Task Success Rate |

### 7.2 단계별 평가 목표

| 단계 | 벤치마크 | 목표 |
|------|----------|------|
| Stage 1 완료 | ScreenSpot | ≥ 75% Accuracy |
| Stage 2 완료 | Mind2Web (Step SR) | ≥ 55% |
| Stage 2 완료 | AITW (Overall) | ≥ 70% |
| Phase 2 완료 | OSWorld (SR) | ≥ 15% |
| Phase 2 완료 | Mind2Web (Step SR) | ≥ 60% (Stage 2 대비 +5%p) |

### 7.3 평가 프로토콜

- 각 Stage 완료 후 주요 벤치마크에서 평가 수행
- 평가는 vLLM을 활용한 배치 추론으로 실행
- Greedy decoding (temperature=0) 사용
- 결과는 `eval/results/` 디렉토리에 JSON 포맷으로 저장

---

## 8. 설정 파일

### 8.1 `configs/stage1.yaml`

```yaml
# Stage 1: GUI World Modeling (Full Finetuning)
model:
  name_or_path: "Qwen/Qwen3-VL-8B-Instruct"
  attn_implementation: "flash_attention_2"
  torch_dtype: "bfloat16"

data:
  train_data:
    - path: "data/processed/grounding"
      ratio: 0.3
    - path: "data/processed/ocr"
      ratio: 0.2
    - path: "data/processed/caption"
      ratio: 0.2
    - path: "data/processed/state_diff"
      ratio: 0.15
    - path: "data/processed/element_qa"
      ratio: 0.15
  max_length: 4096
  image_max_pixels: 1003520       # ~1000x1000
  image_min_pixels: 3136          # 56x56

training:
  method: "full"                   # Full finetuning
  output_dir: "checkpoints/stage1"
  num_train_epochs: 1
  per_device_train_batch_size: 2
  gradient_accumulation_steps: 8   # effective batch 128 (8 GPUs)
  learning_rate: 1.0e-5
  weight_decay: 0.01
  warmup_ratio: 0.03
  lr_scheduler_type: "cosine"
  bf16: true
  gradient_checkpointing: true
  save_strategy: "steps"
  save_steps: 500
  logging_steps: 10
  dataloader_num_workers: 8

deepspeed:
  stage: 3                         # ZeRO-3
  offload_optimizer: false
  offload_param: false
```

### 8.2 `configs/stage2.yaml`

```yaml
# Stage 2: Task Finetuning (LoRA)
model:
  name_or_path: "checkpoints/stage1/final"
  attn_implementation: "flash_attention_2"
  torch_dtype: "bfloat16"

lora:
  r: 64
  lora_alpha: 128
  target_modules:
    - "q_proj"
    - "k_proj"
    - "v_proj"
    - "o_proj"
    - "gate_proj"
    - "up_proj"
    - "down_proj"
  lora_dropout: 0.05
  task_type: "CAUSAL_LM"

data:
  train_data:
    - path: "data/processed/short_horizon"
      ratio: 0.5
    - path: "data/processed/long_horizon"
      ratio: 0.5
  max_length: 8192
  image_max_pixels: 1003520
  image_min_pixels: 3136

training:
  method: "lora"
  output_dir: "checkpoints/stage2"
  num_train_epochs: 3
  per_device_train_batch_size: 2
  gradient_accumulation_steps: 4   # effective batch 64 (8 GPUs)
  learning_rate: 2.0e-4
  weight_decay: 0.01
  warmup_ratio: 0.03
  lr_scheduler_type: "cosine"
  bf16: true
  gradient_checkpointing: true
  save_strategy: "steps"
  save_steps: 500
  logging_steps: 10

deepspeed:
  stage: 2                         # ZeRO-2
```

### 8.3 `configs/rl.yaml`

```yaml
# Phase 2: RL Finetuning (GRPO)
model:
  name_or_path: "checkpoints/stage2/final"
  torch_dtype: "bfloat16"

lora:
  r: 64
  lora_alpha: 128
  target_modules:
    - "q_proj"
    - "k_proj"
    - "v_proj"
    - "o_proj"
    - "gate_proj"
    - "up_proj"
    - "down_proj"
  lora_dropout: 0.0

rl:
  algorithm: "grpo"
  group_size: 8                    # responses per prompt
  kl_coef: 0.03
  clip_range: 0.2
  max_response_length: 2048
  temperature: 0.7                 # sampling temperature for exploration
  reward:
    short_horizon_weight: 0.5
    long_horizon_weight: 0.5

training:
  output_dir: "checkpoints/rl"
  num_train_epochs: 1
  per_device_train_batch_size: 1
  gradient_accumulation_steps: 8
  learning_rate: 5.0e-6
  warmup_ratio: 0.05
  lr_scheduler_type: "cosine"
  bf16: true
  save_strategy: "steps"
  save_steps: 200
  logging_steps: 5

vllm:
  tensor_parallel_size: 2          # RL 샘플링용 vLLM 설정
  gpu_memory_utilization: 0.85
```

---

## 9. CLI 인터페이스

### 9.1 기본 실행

```bash
# 전체 파이프라인 순차 실행
python -m monkey.train --config configs/stage1.yaml --stage stage1
python -m monkey.train --config configs/stage2.yaml --stage stage2
python -m monkey.train --config configs/rl.yaml --stage rl
```

### 9.2 개별 명령어

```bash
# 데이터 전처리
python -m monkey.data.download --dataset screenspot mind2web aitw
python -m monkey.data.preprocess --stage stage1 --output data/processed/

# Stage 1: GUI World Modeling
torchrun --nproc_per_node=8 -m monkey.train \
  --config configs/stage1.yaml \
  --stage stage1

# Stage 2: Task Finetuning
torchrun --nproc_per_node=8 -m monkey.train \
  --config configs/stage2.yaml \
  --stage stage2

# Phase 2: RL
python -m monkey.train \
  --config configs/rl.yaml \
  --stage rl

# 평가
python -m monkey.eval \
  --model checkpoints/stage2/final \
  --benchmarks screenspot mind2web aitw \
  --output eval/results/stage2.json

# LoRA 병합
python -m monkey.model.merge \
  --base Qwen/Qwen3-VL-8B-Instruct \
  --adapter checkpoints/rl/final \
  --output models/monkey-8b-v1

# 데모
python -m monkey.inference.demo \
  --model models/monkey-8b-v1 \
  --port 7860
```

---

## 10. 비기능 요구사항

### 10.1 에러 처리

| 상황 | 대응 |
|------|------|
| GPU OOM | gradient accumulation 자동 증가, batch size 축소 |
| 학습 중단 | 최신 체크포인트에서 자동 재개 (--resume_from_checkpoint) |
| 데이터 손상 (이미지 로드 실패) | 해당 샘플 스킵, 로그 기록 |
| NaN loss 발생 | 학습 중단, loss spike 지점 로그 기록 |

### 10.2 로깅

```
포맷: %(asctime)s [%(levelname)s] %(name)s: %(message)s
레벨: INFO

필수 로그 항목:
  - 학습 loss (train/loss, train/learning_rate)
  - 평가 메트릭 (eval/accuracy, eval/loss)
  - GPU 메모리 사용량
  - 학습 throughput (samples/sec, tokens/sec)
  - 체크포인트 저장 이벤트

로깅 백엔드:
  - Weights & Biases (wandb)
  - TensorBoard (fallback)
```

### 10.3 성능 목표

| 메트릭 | 목표 |
|--------|------|
| Stage 1 학습 시간 | ≤ 48h (8×A100 80GB) |
| Stage 2 학습 시간 | ≤ 24h (8×A100 80GB) |
| RL 학습 시간 | ≤ 48h (8×A100 80GB) |
| 추론 지연 (단일 스크린샷) | ≤ 3s (A100 1장) |
| 추론 처리량 | ≥ 10 req/s (vLLM, A100 1장) |

### 10.4 보안

- 학습 데이터에 개인정보(PII)가 포함되지 않도록 전처리 단계에서 필터링
- 모델 체크포인트는 Hugging Face Hub 또는 사설 스토리지에 저장
- wandb 프로젝트는 private 설정

---

## 11. 의존성

### 11.1 시스템

```
- Python >= 3.10
- CUDA >= 12.1
- NVIDIA GPU (A100 80GB × 8 권장, H100 호환)
- OS: Linux (Ubuntu 22.04+)
```

### 11.2 핵심 패키지

| 패키지 | 버전 | 용도 |
|--------|------|------|
| torch | ≥ 2.4.0 | 딥러닝 프레임워크 |
| transformers | ≥ 4.48.0 | Qwen3-VL 모델 로딩 및 학습 |
| peft | ≥ 0.14.0 | LoRA 어댑터 |
| trl | ≥ 0.14.0 | GRPO RL 학습 |
| deepspeed | ≥ 0.16.0 | 분산 학습 (ZeRO) |
| vllm | ≥ 0.7.0 | 배치 추론, RL 샘플링 |
| datasets | ≥ 3.0.0 | 데이터셋 관리 |
| accelerate | ≥ 1.2.0 | 분산 학습 유틸리티 |
| wandb | ≥ 0.19.0 | 실험 로깅 |
| Pillow | ≥ 10.0 | 이미지 처리 |
| flash-attn | ≥ 2.7.0 | Flash Attention 2 |
| qwen-vl-utils | latest | Qwen-VL 이미지 처리 유틸리티 |
| gradio | ≥ 5.0 | 데모 UI |

---

## 12. 코드 계보

| 원본 | 대상 | 변경 수준 |
|------|------|----------|
| UI-TARS (ByteDance) | 학습 파이프라인 설계, curriculum 구조 | 아키텍처 참고, 신규 구현 |
| OS-Atlas (OS-Copilot) | 데이터 처리 파이프라인, grounding corpus | 데이터 포맷 참고 |
| ShowUI (ShowLab) | Visual token selection | 기법 참고 |
| GUI-Libra | RL post-training recipe, action-aligned reasoning | 기법 참고 |
| MobileRL / AdaGRPO | Difficulty-adaptive GRPO, failure curriculum | RL 전략 참고 |
| UGround (OSU NLP) | Universal visual grounding | grounding 데이터/기법 참고 |
| LLaMA-Factory | SFT 학습 코드 | 학습 유틸리티 참고 |
| OpenRLHF / veRL | RL 학습 코드 | GRPO 구현 참고 |

**참고**:
- https://github.com/bytedance/UI-TARS
- https://github.com/OS-Copilot/OS-Atlas
- https://github.com/showlab/ShowUI
- https://osu-nlp-group.github.io/UGround/
- https://github.com/hiyouga/LLaMA-Factory
- https://github.com/OpenRLHF/OpenRLHF
- https://verl.readthedocs.io/en/latest/algo/grpo.html

---

## 13. 용어 정리

| 용어 | 정의 |
|------|------|
| GUI Foundation Model | 다양한 GUI 환경(웹, 모바일, 데스크톱)에서 범용적으로 동작하는 시각-언어 기반 모델 |
| Curriculum Learning | 쉬운 태스크에서 어려운 태스크로 단계적으로 학습 난이도를 높여가는 학습 전략 |
| World Modeling | GUI 화면의 구조, 요소, 상태를 이해하고 변화를 예측하는 능력 |
| Visual Grounding | 자연어 설명에 해당하는 GUI 요소의 위치(좌표)를 이미지에서 찾는 태스크 |
| Short-horizon Task | 1~3 스텝 내에 완료되는 단기 GUI 조작 태스크 |
| Long-horizon Task | 4 스텝 이상의 다단계 GUI 조작 시퀀스가 필요한 장기 태스크 |
| SFT (Supervised Fine-Tuning) | 정답 레이블이 있는 데이터로 모델을 지도 학습하는 방법 |
| LoRA (Low-Rank Adaptation) | 모델의 일부 가중치에 저랭크 어댑터를 추가하여 효율적으로 학습하는 기법 |
| GRPO (Group Relative Policy Optimization) | 그룹 내 상대적 보상을 기준으로 정책을 최적화하는 강화학습 알고리즘 |
| Full Finetuning | 모델의 전체 파라미터를 업데이트하는 학습 방법 |
| Trajectory | 태스크 수행 과정에서 생성되는 (스크린샷, 액션) 시퀀스 |
| Action Space | 모델이 생성할 수 있는 GUI 조작 행동의 집합 (click, type, scroll 등) |
| KL Divergence | RL 학습 시 정책이 reference 모델에서 과도하게 벗어나지 않도록 제약하는 발산 척도 |
| RLVR (RL with Verifiable Rewards) | 학습된 reward model 대신 규칙 기반 검증 가능한 reward를 사용하는 RL 방식 |
| AdaGRPO | GRPO에 난이도 적응형 전략(shortest-path reward, adaptive replay, failure curriculum)을 결합한 변형 |
| Interleaved-MRoPE | Qwen3-VL의 위치 인코딩 방식, 시각과 텍스트 토큰의 위치 정보를 교차 인코딩 |
| DeepStack | Qwen3-VL의 시각-텍스트 정렬 기법, long-horizon temporal reasoning 강화 |

---

## 부록: 프로젝트 디렉토리 구조

```
Monkey/
├── PRD.md                    # 이 문서
├── pyproject.toml
├── requirements.txt
├── configs/
│   ├── stage1.yaml           # Stage 1 학습 설정
│   ├── stage2.yaml           # Stage 2 학습 설정
│   ├── rl.yaml               # RL 학습 설정
│   └── collection/
│       ├── default.yaml
│       └── apps.yaml
├── collection/              # 데이터 수집 서버 (Python)
│   ├── cli.py
│   ├── orchestrator.py
│   ├── server.py
│   ├── adb/
│   ├── handlers/
│   ├── fallback/
│   ├── annotation/
│   ├── format/
│   └── storage/
├── app/                     # Android 수집 앱 (Kotlin)
├── monkey/
│   ├── __init__.py
│   ├── train.py              # 학습 엔트리포인트
│   ├── data/
│   │   ├── __init__.py
│   │   ├── download.py
│   │   ├── preprocessing.py
│   │   ├── format_converter.py
│   │   ├── dataset.py
│   │   ├── collator.py
│   │   ├── datasets/         # 데이터셋별 변환기
│   │   └── prompts/          # 프롬프트 템플릿
│   ├── model/
│   │   ├── __init__.py
│   │   ├── loader.py
│   │   ├── lora.py
│   │   └── merge.py
│   ├── training/
│   │   ├── __init__.py
│   │   ├── sft_trainer.py
│   │   ├── rl_trainer.py
│   │   ├── reward.py
│   │   └── callbacks.py
│   ├── eval/
│   │   ├── __init__.py
│   │   ├── evaluator.py
│   │   ├── metrics.py
│   │   ├── benchmarks/
│   │   └── inference.py
│   └── inference/
│       ├── __init__.py
│       ├── server.py
│       ├── client.py
│       └── demo.py
├── data/
│   ├── raw/
│   │   └── sessions/        # 수집 세션 데이터
│   └── processed/            # 전처리된 데이터
├── checkpoints/              # 학습 체크포인트
├── models/                   # 최종 병합 모델
├── eval/
│   └── results/              # 평가 결과
└── scripts/                  # 실행 스크립트
```
