# Monkey - GUI World Modeling Data Collection Pipeline

Monkey 프로젝트의 Phase 1 Stage 1 (GUI World Modeling) 학습을 위한 자동 데이터 수집 파이프라인.

Android Virtual Device(AVD)에서 **Smart Monkey**(XML 기반 지능형 액션 선택)로 UI 이벤트를 생성하고, AccessibilityService 앱이 UI 변화를 감지하여 스크린샷 + XML을 서버로 전송한다. 수집된 raw data는 annotation pipeline을 통해 grounding/OCR/state_diff/element_qa/world_modeling 학습 데이터로 자동 변환된다.

## Architecture

```
Server/ (Python)                    AVD
┌────────────────────┐             ┌──────────────────────────────┐
│  Smart Monkey      │── ADB ────▶│  Target App                  │
│  (XML 파싱 →       │  input     │  ← adb input tap/swipe/text  │
│   지능형 액션 선택)  │  commands  │    (요소 대상 정밀 이벤트)     │
│                    │             │                              │
│  TCP Server        │◀── TCP ───│  App/ (AccessibilityService)  │
│  (데이터 수신 +     │             │  • UI 변화 감지               │
│   XML 동기화)       │             │  • 화면 안정화 → 캡처 → 전송  │
│                    │             │  • 앱 이탈 감지 → back 실행   │
│  Annotation        │             └──────────────────────────────┘
│  Pipeline          │
│  (수집 후 변환)     │
└────────────────────┘
```

### Smart Monkey vs Random Monkey

| 항목 | Random Monkey (`adb shell monkey`) | Smart Monkey |
|------|--------------------------------------|-------------|
| 이벤트 대상 | 랜덤 좌표 | XML에서 식별한 UI 요소 |
| EditText 처리 | 불가 | 자동 감지 → 샘플 텍스트 입력 |
| 가중치 조정 | 고정 비율 | UI 상태에 따라 동적 조정 |
| 동기화 | 없음 (fire-and-forget) | 액션→캡처 1:1 동기화 |
| 탐색 효율 | ~60% 유효 전이 | ~85%+ 유효 전이 |

### 수집 흐름

1. Server → Smart Monkey가 앱 실행 후 초기 XML 획득
2. Smart Monkey → XML 파싱 → clickable/editable/scrollable 요소 식별 → 가중치 기반 액션 선택
3. Server → `adb input tap/swipe/text` 로 정확한 UI 요소에 이벤트 실행
4. App(AccessibilityService) → UI 변화 감지 → **화면 안정화 대기** → **시각적 변화 확인** → 스크린샷 + XML을 TCP로 Server에 전송
5. Server → 수신 XML로 다음 액션 결정 (step-by-step 루프)
6. App → top_package != target_package 감지 시 → Server에 `E` 알림 + back 실행
7. 수집 완료 후 → Annotation Pipeline으로 raw data → Stage 1 학습 포맷 변환

### TCP 프로토콜

| 메시지 | 코드 | 페이로드 |
|--------|------|---------|
| Screenshot | `S` | size + `\n` + JPEG bytes |
| XML | `X` | top_pkg + `\n` + target_pkg + `\n` + size + `\n` + XML bytes |
| External App | `E` | JSON + `\n` |
| Finish | `F` | (없음) |

## Requirements

### System

- Python >= 3.10
- Android SDK (adb, avdmanager, emulator)
- Java 17+ (Android app 빌드용)

### Python Dependencies

```bash
pip install -e .

# LLM 캡셔닝이 필요한 경우
pip install -e ".[annotation]"
```

| 패키지 | 용도 |
|--------|------|
| pyyaml | YAML 설정 파일 파싱 |
| Pillow | 이미지 처리 |
| loguru | 로깅 |
| openai (optional) | LLM 기반 스크린샷 캡셔닝 |

## Quick Start

### 1. AVD 셋업

```bash
# AVD 생성 및 시작
./scripts/setup_avd.sh
```

또는 수동으로:

```bash
# SDK 이미지 다운로드
sdkmanager "system-images;android-34;google_apis;x86_64"

# AVD 생성
avdmanager create avd -n monkey_collector \
  -k "system-images;android-34;google_apis;x86_64" \
  --device "pixel_6"

# 에뮬레이터 시작
emulator -avd monkey_collector -no-window -no-audio &
adb wait-for-device
```

### 2. Android 앱 빌드 및 설치

```bash
cd app/
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

설치 후 AVD에서:
1. Settings → Accessibility → MonkeyCollector → 활성화
2. MonkeyCollector 앱 실행 → Server IP/Port/Target Package 설정

> AVD에서 호스트 머신 접속 시 IP: `10.0.2.2`

### 3. 데이터 수집

```bash
# 단일 앱 수집 (Smart Monkey 100 steps)
monkey-collect run --app com.android.calculator2 --events 100

# 시드 지정 + 액션 딜레이 조정
monkey-collect run --app com.android.calculator2 --events 200 --seed 123 --action-delay 300

# apps.yaml에 정의된 전체 앱 배치 수집
monkey-collect batch --apps-config configs/collection/apps.yaml

# annotation만 실행 (이미 수집된 데이터)
monkey-collect annotate --session <session_id>

# 전체 파이프라인 (수집 + annotation)
monkey-collect pipeline --apps-config configs/collection/apps.yaml
```

또는 스크립트 사용:

```bash
./scripts/collect.sh
```

## CLI Reference

```
monkey-collect <command> [options]
```

### Commands

| 명령 | 설명 | 주요 옵션 |
|------|------|----------|
| `run` | 단일 앱 수집 | `--app` (필수), `--events`, `--seed`, `--action-delay`, `--config` |
| `batch` | 다수 앱 배치 수집 | `--apps-config`, `--config` |
| `annotate` | Annotation 실행 | `--session` (생략 시 전체), `--config` |
| `pipeline` | 수집 + annotation | `--apps-config`, `--config` |

### Examples

```bash
# Calculator 앱에서 Smart Monkey 200 steps
monkey-collect run --app com.android.calculator2 --events 200

# 시드 고정 (재현 가능한 탐색)
monkey-collect run --app com.android.calculator2 --events 100 --seed 42

# 커스텀 설정으로 배치 수집
monkey-collect batch \
  --config configs/collection/default.yaml \
  --apps-config configs/collection/apps.yaml

# 특정 세션만 annotation
monkey-collect annotate --session abc12345
```

## Configuration

### configs/collection/default.yaml

```yaml
collection:
  smart_monkey:
    action_weights:
      tap: 0.60               # 탭 (클릭 가능한 요소 대상)
      press_back: 0.10        # 뒤로가기
      swipe: 0.10             # 스와이프 (스크롤 가능 요소 대상)
      input_text: 0.10        # 텍스트 입력 (EditText 대상)
      long_press: 0.05        # 롱프레스
      press_home: 0.05        # 홈 버튼
    action_delay_ms: 500      # 액션 간 대기 시간 (ms)
    seed: 42                  # 랜덤 시드 (재현성)
    max_retries_on_crash: 3   # 크래시 시 복구 최대 시도
    sample_texts:             # EditText 자동 입력 텍스트
      - "Hello World"
      - "Test Note"
      - "test@example.com"
  session:
    max_events_per_app: 100   # 앱당 기본 step 수
  server:
    host: "0.0.0.0"
    port: 12345
  fallback:
    max_back_attempts: 3      # 앱 이탈 시 back 최대 시도
    allowed_packages:         # 이탈로 간주하지 않는 패키지
      - com.android.systemui
      - com.android.permissioncontroller

annotation:
  grounding: { enabled: true, min_element_area: 100 }
  ocr: { enabled: true, min_text_length: 1 }
  state_diff: { enabled: true, min_changes: 1 }
  element_qa: { enabled: true, templates_per_screen: 5 }
  llm_caption: { enabled: false, provider: openai, model: gpt-4o-mini }

format:
  output_dir: data/processed
  normalize_coords: true      # bbox 좌표를 [0, 1000] 범위로 정규화
```

### Smart Monkey 가중치 동적 조정

Smart Monkey는 현재 화면의 UI 요소 유형에 따라 액션 가중치를 자동 조정한다:

| 조건 | 조정 |
|------|------|
| EditText 필드 존재 | `input_text` 가중치 ↑ 25% |
| clickable 요소 없음 | `tap` 가중치 ↓ 5% |
| scrollable 요소 없음 | `swipe` 가중치 ↓ 2% |

### AVD 설치 앱 목록 (Medium Phone API 36.0)

수집 대상으로 사용 가능한 앱 목록. `apps.yaml` 작성 시 참고.

#### 서드파티 앱

| 패키지 | 앱 | 카테고리 |
|--------|-----|----------|
| `net.gsantner.markor` | Markor | 마크다운 에디터 |
| `com.arduia.expense` | Expense | 가계부 |
| `com.flauschcode.broccoli` | Broccoli | 레시피 |
| `com.simplemobiletools.gallery.pro` | Simple Gallery Pro | 갤러리 |
| `com.simplemobiletools.calendar.pro` | Simple Calendar Pro | 캘린더 |
| `com.simplemobiletools.smsmessenger` | Simple SMS Messenger | 메시지 |
| `com.simplemobiletools.draw.pro` | Simple Draw Pro | 그리기 |
| `org.videolan.vlc` | VLC | 미디어 플레이어 |
| `net.osmand` | OsmAnd | 지도/내비 |
| `org.tasks` | Tasks | 할일 관리 |
| `net.cozic.joplin` | Joplin | 노트 |
| `code.name.monkey.retromusic` | Retro Music | 음악 플레이어 |
| `com.dimowner.audiorecorder` | Audio Recorder | 녹음 |
| `de.dennisguse.opentracks` | OpenTracks | 운동 기록 |
| `com.google.androidenv.miniwob` | MiniWoB | 벤치마크 |

#### 주요 시스템 앱

| 패키지 | 앱 |
|--------|-----|
| `com.android.camera2` | 카메라 |
| `com.google.android.contacts` | 연락처 |
| `com.google.android.calendar` | 캘린더 |
| `com.google.android.deskclock` | 시계 |
| `com.google.android.dialer` | 전화 |
| `com.android.settings` | 설정 |
| `com.google.android.apps.maps` | Google Maps |
| `com.google.android.apps.youtube.music` | YouTube Music |

> **참고**: `com.android.calculator2`(계산기)는 API 36에 기본 포함되지 않음.

### configs/collection/apps.yaml

```yaml
apps:
  # 시스템 앱
  - { package: com.android.settings, name: Settings, source: system, max_events: 150 }
  - { package: com.google.android.contacts, name: Contacts, source: system, max_events: 100 }
  - { package: com.google.android.deskclock, name: Clock, source: system, max_events: 80 }
  - { package: com.google.android.dialer, name: Phone, source: system, max_events: 80 }
  # 서드파티 앱 (AVD에 설치 완료)
  - { package: net.gsantner.markor, name: Markor, source: system, max_events: 100 }
  - { package: com.arduia.expense, name: Expense, source: system, max_events: 100 }
  - { package: org.tasks, name: Tasks, source: system, max_events: 100 }
  - { package: net.cozic.joplin, name: Joplin, source: system, max_events: 100 }
  - { package: com.simplemobiletools.gallery.pro, name: SimpleGallery, source: system, max_events: 80 }
  - { package: com.simplemobiletools.calendar.pro, name: SimpleCalendar, source: system, max_events: 80 }
```

## Project Structure

```
Monkey/
├── PRD.md                          # 프로젝트 요구사항 문서
├── README.md                       # 이 문서
├── pyproject.toml                  # Python 패키지 설정
├── requirements.txt
│
├── configs/collection/
│   ├── default.yaml                # 수집/annotation 기본 설정
│   └── apps.yaml                   # 대상 앱 목록
│
├── collection/                     # Server (Python)
│   ├── cli.py                      # CLI 엔트리포인트
│   ├── orchestrator.py             # 수집 오케스트레이터 (Smart Monkey + TCP 서버 조율)
│   ├── server.py                   # TCP 서버 (Android 앱에서 데이터 수신 + XML 동기화)
│   ├── explorer/
│   │   ├── smart_monkey.py         # Smart Monkey (XML 기반 지능형 액션 선택)
│   │   └── action_space.py         # Action 데이터클래스 (Tap, Swipe, InputText 등)
│   ├── adb/
│   │   ├── client.py               # ADB 명령 래퍼 (input tap/swipe/text 포함)
│   │   └── monkey.py               # (레거시) adb shell monkey 실행/로그 파싱
│   ├── handlers/
│   │   └── message_handlers.py     # TCP 메시지 타입 상수
│   ├── fallback/
│   │   └── monitor.py              # 앱 이탈 감지 및 기록
│   ├── annotation/
│   │   ├── xml_parser.py           # uiautomator XML → UIElement 리스트
│   │   ├── xml_encoder.py          # uiautomator XML → HTML-style XML (parseXML.py 포팅)
│   │   ├── grounding.py            # element → bbox + description QA
│   │   ├── ocr_extractor.py        # text elements → OCR QA
│   │   ├── state_diff.py           # consecutive XML → 변화 감지 QA
│   │   ├── element_qa.py           # element 속성 → QA 템플릿
│   │   ├── world_modeling.py       # before/after → GUI-Model stage1 형식
│   │   └── llm_annotator.py        # LLM 스크린샷 캡셔닝 (선택적)
│   ├── format/
│   │   └── converter.py            # raw → JSONL 통합 변환기
│   └── storage/
│       └── writer.py               # 세션별 raw data 저장
│
├── app/                            # Android App (Kotlin)
│   ├── build.gradle.kts
│   ├── settings.gradle.kts
│   └── app/src/main/
│       ├── AndroidManifest.xml
│       ├── res/xml/accessibility_config.xml
│       └── java/com/monkey/collector/
│           ├── CollectorService.kt     # AccessibilityService (핵심)
│           ├── ScreenStabilizer.kt     # MediaProjection 화면 안정화 (NEW)
│           ├── BitmapComparator.kt     # 비트맵 비교 유틸 (NEW)
│           ├── MediaProjectionHelper.kt # MediaProjection 권한 전달 (NEW)
│           ├── TcpClient.kt            # TCP 데이터 전송
│           ├── ScreenCapture.kt        # 스크린샷 캡처 (API 30+)
│           ├── XmlDumper.kt            # AccessibilityNodeInfo → XML
│           └── MainActivity.kt         # 설정 UI + MediaProjection 권한
│
├── data/
│   ├── raw/sessions/               # 세션별 원본 데이터
│   │   └── {session_id}/
│   │       ├── screenshots/        # {step}.png
│   │       ├── xml/                # {step}.xml
│   │       ├── events.jsonl        # monkey 이벤트 로그
│   │       └── metadata.json       # 세션 메타데이터
│   └── processed/                  # annotation 결과
│       ├── grounding.jsonl
│       ├── ocr.jsonl
│       ├── state_diff.jsonl
│       ├── element_qa.jsonl
│       ├── world_modeling.jsonl    # GUI-Model stage1 호환
│       ├── caption.jsonl
│       └── images/                 # 복사된 스크린샷
│
└── scripts/
    ├── setup_avd.sh                # AVD 생성 + 시작
    └── collect.sh                  # 배치 수집 + annotation 실행
```

## Visual Screen Change Detection

캡처 품질 향상을 위해 [computer-use-preview-for-mobile](https://github.com/nicholasgcoles/computer-use-preview-for-mobile) 참조 구현의 비주얼 안정화 기법을 통합했다.

### 원리

AccessibilityEvent가 트리거된 후, 실제 캡처 전에 두 가지 검증 단계를 수행한다:

1. **Screen Stabilization** (`ScreenStabilizer.waitForStable()`): MediaProjection으로 100px 저해상도 프레임을 500ms 간격으로 캡처하여 연속 비교. 3회 연속 2% 이내 차이면 "안정" 판정
2. **Visual Change Check** (`ScreenStabilizer.hasVisualChange()`): 이전 안정 프레임과 현재 프레임을 비교하여 실제 화면 변화가 있는 경우에만 고해상도 캡처 진행

### 파라미터

| 파라미터 | 값 | 설명 |
|---------|------|------|
| `TARGET_WIDTH` | 100px | 비교용 저해상도 너비 |
| `STABILITY_THRESHOLD` | 0.02 (2%) | 안정 판정 임계값 |
| `REQUIRED_STABLE_FRAMES` | 3 | 연속 안정 프레임 수 |
| `CHECK_INTERVAL_MS` | 500ms | 프레임 비교 간격 |
| `MAX_ATTEMPTS` | 30 (~15초) | 최대 대기 시도 |

### 효과

- mid-animation/transition 캡처 방지 → 깨끗한 학습 데이터
- 동일 화면 중복 캡처 방지 → 데이터 효율성
- AccessibilityEvent가 놓치는 시각적 변화도 감지 가능

### 컴포넌트

| 파일 | 역할 |
|------|------|
| `BitmapComparator.kt` | 두 Bitmap의 픽셀 차이 계산 (RGBA 32-bit) |
| `ScreenStabilizer.kt` | MediaProjection 저해상도 캡처 + 안정화 대기 + 변화 감지 |
| `MediaProjectionHelper.kt` | Activity→Service 간 MediaProjection 권한 전달 |

---

## Annotation Pipeline

수집된 raw data를 6종의 학습 데이터로 변환한다.

### 모듈별 상세

| 모듈 | 입력 | 출력 | 포맷 | 산출량/step |
|------|------|------|------|------------|
| `xml_parser` | raw XML | `list[UIElement]` | (중간 결과) | - |
| `xml_encoder` | raw XML | HTML-style XML | (중간 결과) | - |
| `grounding` | UIElement (clickable/visible) | `grounding.jsonl` | Format A | ~10개 |
| `ocr_extractor` | UIElement (text 있는 것) | `ocr.jsonl` | Format A | ~8개 |
| `state_diff` | 연속 XML 쌍 비교 | `state_diff.jsonl` | Format A | 0~1개 |
| `element_qa` | UIElement 속성 | `element_qa.jsonl` | Format A | ~5개 |
| `world_modeling` | before/after XML + action | `world_modeling.jsonl` | Format B | 0~1개 |
| `llm_annotator` | 스크린샷 → LLM API | `caption.jsonl` | Format A | 선택적 |

### xml_encoder: HTML-style XML 변환

MobileGPT-V2의 `parseXML.py`를 Python으로 포팅한 모듈. uiautomator XML을 LLM 친화적인 HTML-style XML로 변환한다.

**변환 규칙**:

| Android class | HTML tag | 조건 |
|--------------|----------|------|
| `EditText` | `<input>` | - |
| `TextView` | `<p>` | text를 element.text로 |
| `ImageView` | `<img>` | - |
| `Button` / clickable | `<button>` | - |
| checkable=true | `<checker>` | checked 속성 포함 |
| scrollable=true | `<scroll>` | - |
| FrameLayout/LinearLayout/... | `<div>` | - |

**후처리**: 빈 bounds 노드 제거, 단일 자식 wrapper 단순화, scroll 내 중복 제거

```python
from collection.annotation.xml_encoder import encode_to_html_xml

# 입력: uiautomator XML
raw_xml = open("data/raw/sessions/abc123/xml/0001.xml").read()

# 출력: HTML-style XML (bounds 제거, LLM용)
encoded = encode_to_html_xml(raw_xml)
# <div index="0">
#   <p id="toolbar" index="0">Calculator</p>
#   <button id="digit_7" text="7" index="2" />
# </div>
```

## Output Formats

### Format A: Stage 1 통합 포맷

grounding, OCR, state_diff, element_qa, caption용.

```json
{
  "id": "monkey_{session}_{step}_{task_type}_{idx}",
  "image": "data/processed/images/{session}_{step}.png",
  "conversations": [
    {"role": "user", "content": "<image>\n{task_prompt}"},
    {"role": "assistant", "content": "{answer}"}
  ],
  "task_type": "grounding | ocr | state_diff | element_qa | caption",
  "metadata": {
    "source": "monkey_collection",
    "platform": "android",
    "resolution": [1080, 2400],
    "app_package": "com.android.calculator2",
    "session_id": "abc123",
    "step": 42
  }
}
```

### Format B: GUI-Model ShareGPT 포맷

world_modeling용. `gui-model_stage1.jsonl` 호환.

```json
{
  "messages": [
    {
      "from": "system",
      "value": "You are a mobile UI transition predictor.\nGiven the current screen represented as html-style XML and an action description, predict the next screen's html-style XML after the action is executed."
    },
    {
      "from": "human",
      "value": "<image>\n## Current State\n{before_encoded_html_xml}\n\n## Action\n{action_json}"
    },
    {
      "from": "gpt",
      "value": "{after_encoded_html_xml}"
    }
  ],
  "images": ["images/{session}_{step}.png"]
}
```

## Fallback Mechanism

Smart Monkey 또는 앱 자체가 타겟 앱 외부로 벗어나는 것을 다층 방어한다.

### Smart Monkey 측 (서버)

1. 매 step 후 `get_current_package()` → 타겟 앱 이탈 확인
2. 이탈 시 `press_back()` → 확인 → 여전히 이탈이면 `launch_app()` 강제 실행
3. 크래시/에러 시 `recover()` → home → launch_app → 2초 대기

### App 측 (실시간)

1. `onAccessibilityEvent` → `TYPE_WINDOW_STATE_CHANGED` 시 top package 확인
2. target_package와 다르면 → Server에 `E` 메시지 전송 + `GLOBAL_ACTION_BACK` 실행
3. 3회 연속 back 실패 → `am start`로 타겟 앱 강제 실행

### Server 측 (데이터 필터링)

1. `E` 메시지 수신 → 해당 step에 `external_app` 플래그
2. Annotation 시 플래그된 step 자동 제외
3. 연속 이탈 횟수 로깅 (앱 안정성 지표)

## Expected Data Yield

시스템 앱 10개 + F-Droid 40개 = 50개 앱, 앱당 100 이벤트 기준:

| task_type | 예상 산출량 |
|-----------|-----------|
| grounding | ~50K |
| ocr | ~40K |
| state_diff | ~3K (유효 전이 ~60%) |
| element_qa | ~25K |
| world_model | ~2.5K |
| caption | ~500 (10% LLM) |
| **합계** | **~121K** |

> State Diff 50K 목표 → 500개 앱 x 200 이벤트 (스케일업 Phase)

## Code Lineage

| 원본 | 대상 | 참고 방식 |
|------|------|----------|
| MobileGPT-V2 `AccessibilityNodeInfoDumper.java` | `XmlDumper.kt` | 로직 참고 → Kotlin 재구현 |
| MobileGPT-V2 `MobileGPTAccessibilityService.java` | `CollectorService.kt` | 패턴 참고 → Kotlin 재구현 |
| MobileGPT-V2 `MobileGPTClient.java` | `TcpClient.kt` | TCP 프로토콜 재사용 |
| MobileGPT-V2 `parseXML.py` | `xml_encoder.py` | Python 직접 포팅 |
| MobileGPT-V2 `Encoder.py` | `xml_encoder.py` | 인코딩 로직 포팅 |
| MobileGPT-V2 `message_handlers.py` | `handlers/` | 구조 참고 → 간소화 |
| MobileForge `smart_monkey.py` | `explorer/smart_monkey.py` | 액션 선택 로직 적응 포팅 |
| MobileForge `action_space.py` | `explorer/action_space.py` | Action 데이터클래스 그대로 포팅 |
| MobileForge `adb_client.py` | `adb/client.py` | 입력 메서드 + 텍스트 이스케이프 포팅 |
| GUI-Model `gui-model_stage1.jsonl` | `world_modeling.py` | 포맷 호환 유지 |

## License

Internal project. Not for redistribution.
