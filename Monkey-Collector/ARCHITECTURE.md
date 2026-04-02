# ARCHITECTURE.md

Monkey-Collector 프로젝트의 아키텍처 문서.

---

## 1. System Overview

### 목적

GUI world model 학습용 Android UI 상태 전이 데이터 자동 수집 파이프라인.

Android 앱에서 실시간으로 UI 상태 전환을 감지하고, Python 서버가 다음 액션을 선택/실행하여 (before_state, action, after_state) 형태의 학습 데이터를 자동으로 생산한다.

### Architecture Pattern

**Client(App)가 전환 감지, Server가 액션 선택 및 오케스트레이션**을 담당하는 분리 구조.

- **Android App** (Kotlin): AccessibilityService 기반. 화면 안정화 감지, 스크린샷 캡처, XML hierarchy 덤프, TCP 전송
- **Python Server**: TCP 수신, UI 트리 파싱, 가중치 기반 액션 선택 (SmartExplorer), ADB 실행, 세션 저장, JSONL 변환

### 핵심 설계 근거

화면 전환 감지를 Client 측에서 수행하는 이유:

| 방식 | 지연 시간 |
|------|-----------|
| MediaProjection 저해상도 캡처 (100px) | < 1ms |
| ADB screencap | 300~800ms |

Client-side 감지로 네트워크 트래픽과 서버 처리량을 최소화하고, 실제 전환이 발생한 경우에만 데이터를 전송한다.

---

## 2. Component Diagram

```
┌─ Android App (Kotlin) ──────────────────┐      ┌─ Python Server ──────────────────────┐
│                                          │      │                                       │
│  MainActivity                            │      │  CLI (monkey-collect)                  │
│    └─ IP/Port 설정, MediaProjection 권한  │      │    └─ run / convert / convert-all       │
│                                          │      │                                       │
│  CollectorService (AccessibilityService) │ TCP  │  CollectionServer                     │
│    ├─ FloatingCollectorButton (▶/■)      │←────→│    └─ P/S/X/E/N/F 프로토콜 파싱        │
│    ├─ ScreenStabilizer                   │      │                                       │
│    │   └─ BitmapComparator               │      │  Collector (orchestration loop)        │
│    ├─ ScreenCapture (API 30+)            │      │    ├─ SmartExplorer (action selection) │
│    ├─ XmlDumper                          │      │    ├─ AdbClient (action execution)     │
│    └─ TcpClient (P/S/X/E/N/F)           │      │    └─ DataWriter (session storage)     │
│                                          │      │                                       │
│  MediaProjectionHelper (singleton)       │      │  Converter (raw → JSONL)               │
└──────────────────────────────────────────┘      │  xml_parser, xml_encoder               │
                                                  └───────────────────────────────────────┘
```

### 수집 루프 흐름

```
App: A11y 이벤트 감지 (debounce 300ms)
  → ScreenStabilizer: 화면 안정화 대기
  → hasVisualChange(): 실제 변화 여부 판별
  → 변화 없음 → N signal 전송 → Server: 다른 element로 재시도
  → 변화 있음 → Screenshot + XML 캡처 → TCP 전송
  → Server: parse → select action → ADB 실행 → 저장
  → 반복
```

---

## 3. Android App

### 3.1 Components

#### CollectorService (`CollectorService.kt`)

AccessibilityService 핵심 서비스. `WINDOW_STATE_CHANGED`와 `WINDOW_CONTENT_CHANGED` 이벤트를 수신하며, 300ms debounce로 중복 이벤트를 필터링한다.

이벤트 처리 사이클:
1. 이벤트 수신 및 debounce 필터링
2. TCP 연결 상태 확인
3. `getTopInteractableRoot()`로 최상위 인터랙터블 윈도우의 root node 및 package name 획득
4. 외부 앱 감지 시: `E` signal 전송 + `press_back` (3회 연속 시 강제 재실행)
5. Worker thread에서: ScreenStabilizer 안정화 대기 → 시각적 변화 검사 → 스크린샷/XML 캡처 → TCP 전송

`EXCLUDED_PACKAGES`로 `com.android.systemui`, `com.android.permissioncontroller`, `com.monkey.collector` 를 필터링한다.

step count, first screen flag를 관리하며, `startCollection()`/`stopCollection()`으로 수집 생명주기를 제어한다.

#### FloatingCollectorButton (`FloatingCollectorButton.kt`)

`TYPE_ACCESSIBILITY_OVERLAY` 기반 플로팅 오버레이 버튼. AccessibilityService가 활성화되어 있으면 `SYSTEM_ALERT_WINDOW` 권한 없이 사용 가능하며, accessibility window 목록에 포함되지 않아 `getTopInteractableRoot()`에 간섭하지 않는다.

- **크기**: 48dp, 원형
- **상태**: START(▶ 초록) / STOP(■ 빨강)
- **드래그**: touch 이동 지원, click 판별 threshold 10px
- **타겟 앱 자동 감지**: START 클릭 시 `service.getCurrentForegroundPackage()`로 foreground 앱의 package name을 자동 획득
- **스크린샷 보호**: 캡처 중 `dismiss()` → 캡처 후 `show()`로 스크린샷에 버튼이 포함되지 않도록 처리

#### ScreenStabilizer (`ScreenStabilizer.kt`)

MediaProjection VirtualDisplay + ImageReader를 사용하여 저해상도(100px 너비) 프레임을 캡처하고, 연속 프레임 간 픽셀 비교로 화면 안정화 및 전환을 감지한다.

**안정화 감지** (`waitForStable()`):
- 500ms 간격으로 저해상도 프레임 캡처
- BitmapComparator로 연속 프레임 비교
- 3개 연속 프레임이 2% 미만 차이일 때 안정화 판정
- 최대 30회 시도 (약 15초), 타임아웃 시에도 캡처 진행

**시각적 변화 감지** (`hasVisualChange()`):
- 현재 프레임과 `lastStableFrame` 비교 (2% threshold)
- 변화 없으면 `false` 반환 → N signal 전송으로 이어짐

**First screen 감지**:
- `saveFirstScreen()`: step 0에서 호출, 기준 프레임 저장
- `isFirstScreen()`: 현재 프레임과 저장된 first screen 비교 (5% threshold, 시계/배지 등 동적 콘텐츠 허용)

#### BitmapComparator (`BitmapComparator.kt`)

두 Bitmap의 픽셀 단위 RGBA 비교. 전체 픽셀 중 차이가 있는 픽셀의 비율을 0.0~1.0으로 반환한다.

#### ScreenCapture (`ScreenCapture.kt`)

API 30+ `AccessibilityService.takeScreenshot()` 래퍼. `HardwareBuffer`에서 `Bitmap`으로 변환한다. `CountDownLatch`를 사용한 동기 캡처 (5초 타임아웃).

#### XmlDumper (`XmlDumper.kt`)

`AccessibilityNodeInfo` 트리를 순회하여 uiautomator 호환 XML을 생성한다.
- 텍스트 정리: 줄바꿈 → 공백, XML escape 처리
- 14개 boolean attribute + bounds + text + resource-id + class + package 캡처

#### TcpClient (`TcpClient.kt`)

P/S/X/E/N/F 프로토콜 구현체.
- 스크린샷: JPEG 90% quality 압축 후 전송
- Thread safety: `synchronized(writeLock)`으로 모든 write 연산 보호
- 연결: 3회 재시도, 2초 간격

#### MainActivity (`MainActivity.kt`)

서버 IP/Port 설정 UI. `ActivityResultContract`로 MediaProjection 권한 요청. `SharedPreferences`에 설정 저장. 타겟 앱은 FloatingCollectorButton에서 자동 감지하므로 별도 입력 불필요.

#### MediaProjectionHelper (`MediaProjectionHelper.kt`)

MediaProjection 권한 결과(`resultCode`, `resultData`)를 Activity에서 Service로 전달하는 singleton 브릿지.

### 3.2 Threading Model

| Thread | 역할 |
|--------|------|
| Main thread | AccessibilityEvent 콜백 수신, UI overlay 업데이트 (`Handler(MainLooper)`) |
| Worker threads | 이벤트별 캡처/XML/TCP 작업 (`Thread { ... }.start()`) |
| TCP writes | `synchronized(writeLock)`으로 직렬화 |
| ScreenStabilizer | Worker thread에서 blocking 대기 (`Thread.sleep` 루프) |

### 3.3 Screen Transition Detection

```
AccessibilityEvent 수신 (debounced 300ms)
    │
    ▼
ScreenStabilizer.waitForStable()
    │  100px 프레임을 500ms 간격으로 캡처
    │  3개 연속 프레임 < 2% diff → 안정화 완료
    │  (최대 30회 = ~15초)
    │
    ▼
ScreenStabilizer.hasVisualChange()
    │  현재 프레임 vs lastStableFrame (2% threshold)
    │
    ├─ 변화 없음 → N signal 전송 → Server가 다른 element로 재시도
    │
    └─ 변화 있음
         │
         ├─ First screen 감지 (step 0이면 saveFirstScreen, isFirstScreen 확인)
         │
         ├─ FloatingButton 숨기기
         │
         ├─ Full screenshot (AccessibilityService.takeScreenshot)
         │
         ├─ XML dump (AccessibilityNodeInfo tree 순회)
         │
         └─ TCP 전송: S(screenshot) + X(xml + meta)
              └─ is_first_screen flag 포함
```

### 3.4 First Screen Protection

- **목적**: 첫 화면에서 `press_back` 실행 시 앱이 종료되어 수집 세션이 중단되는 것을 방지
- **저장**: `saveFirstScreen()` — step 0에서 호출, 기준 프레임을 `firstScreenFrame`에 저장
- **비교**: `isFirstScreen()` — 현재 프레임과 저장된 기준 프레임을 5% threshold로 비교 (시계, 알림 배지 등 동적 콘텐츠 변화를 허용하기 위해 안정화 threshold 2%보다 완화)
- **효과**: Server의 SmartExplorer가 first screen에서 `press_back` 가중치를 0으로 설정하고, `tap`으로 대체

---

## 4. Python Server

### 4.1 Components

#### CollectionServer (`server.py`)

TCP 서버. `0.0.0.0:12345`에서 Android 앱의 연결을 대기한다.

- 바이너리 프로토콜 파싱: single-byte header → 메타데이터 텍스트 → size-prefixed 바이너리
- `Queue` 기반 signal 전달: `wait_for_change_signal()`로 XML 또는 no-change signal을 blocking 대기
- `threading.Event`로 package name/XML 동기화
- Daemon thread에서 client 핸들링

#### Collector (`collector.py`)

메인 오케스트레이션 루프. 전체 수집 세션의 생명주기를 관리한다.

루프 구조:
1. `server.wait_for_change_signal(timeout=15s)` — 앱의 signal 대기
2. Signal에 따라 분기:
   - `None` (timeout): 화면 중앙 tap, 5회 연속 시 세션 종료
   - `"no_change"`: element exclusion + 재시도 (최대 3회), 초과 시 press_back (first screen이면 tap)
   - `"xml"`: 정상 처리 — parse → select → execute → save

#### SmartExplorer (`explorer.py`)

XML 인식 가중치 기반 랜덤 액션 선택기. `adb shell monkey`와 달리 UI 구조를 파악하여 의미 있는 액션을 선택한다.

**기본 가중치**:

| Action | Weight |
|--------|--------|
| tap | 60% |
| press_back | 10% |
| swipe | 10% |
| input_text | 10% |
| long_press | 5% |
| press_home | 0% |

**적응형 가중치 조정**:
- editable field 존재 시: `input_text` → 25%
- clickable element 없을 때: `tap` → 5%
- scrollable element 없을 때: `swipe` → 2%
- first screen: `press_back` → 0%

**Element exclusion**: `_excluded_elements` set으로 시도했지만 화면 변화가 없었던 element의 index를 추적. 화면 전환 성공 시 또는 max retry 후 초기화.

#### AdbClient (`adb.py`)

ADB 명령어 래퍼. `PATH`, `ANDROID_HOME`, `~/Library/Android/sdk` 순서로 자동 탐색.

주요 메서드: `tap`, `swipe`, `input_text` (특수 문자 escaping), `long_press`, `press_back`, `press_home`, `launch_app`, `get_current_package`, `get_device_resolution`

#### DataWriter (`storage.py`)

세션별 디렉토리 구조 관리. incremental step counter로 파일 네이밍.

구조:
```
data/raw/{session_id}/
├── metadata.json
├── screenshots/
├── xml/
└── events.jsonl
```

#### UITree / UIElement (`xml_parser.py`)

uiautomator XML을 flat list로 파싱. `UIElement`는 32개 attribute를 가진다.

쿼리 메서드:
- `get_clickable_elements()`
- `get_editable_elements()`
- `get_scrollable_elements()`

#### xml_encoder (`xml_encoder.py`)

raw XML → LLM 친화적 HTML-style XML 변환 파이프라인.

변환 단계:
1. **reformat**: 의미론적 태그 변환 (EditText → `input`, clickable → `button`, Layout → `div`, ImageView → `img`, TextView → `p`)
2. **simplify**: wrapper 축소 (단일 자식 컨테이너 제거)
3. **encode**: bounds/class 속성 제거
4. **remove redundancies**: 중복 제거

#### Converter (`converter.py`)

Raw 세션 → ShareGPT 형식 JSONL 변환.

연속 XML 쌍 (step i, step i+1)에서 학습 예제 생성:
- **system**: "You are a mobile UI transition predictor..."
- **human**: `<image>` + before XML (HTML-encoded) + action JSON
- **gpt**: after XML (HTML-encoded)

출력: `gui-model_stage1.jsonl`

#### CLI (`cli.py`)

Entry point: `monkey-collect`. Subcommands: `run`, `convert`, `convert-all`.

### 4.2 Collection Loop

```python
for step in range(max_steps):
    signal = server.wait_for_change_signal(timeout=15s)

    if signal is None:              # True timeout (signal 없음)
        timeout_count++
        if timeout_count >= 5: break   # 세션 종료
        tap(screen_center)             # 화면 활성화 시도
        continue

    if signal == "no_change":       # 앱이 시각적 변화 없음 보고
        exclude(last_element)       # 해당 element 제외
        retry_count++
        if retry_count >= 3:
            first_screen? → tap_random : press_back
            reset counters
        else:
            select_different_action(same_ui_tree)  # 다른 element/action 시도
        continue

    if signal == "xml":             # 화면 전환 발생
        reset retry/timeout counters
        clear excluded_elements
        check app_escape (top_pkg != target_pkg)
        save screenshot
        parse UI tree
        select action (SmartExplorer)
        execute action (ADB)
        save xml + event log
```

### 4.3 Error Recovery

| 상황 | 대응 |
|------|------|
| Timeout (signal 없음) | 화면 중앙 tap으로 활성화 시도. 5회 연속 시 세션 종료 |
| 시각적 변화 없음 (N signal) | element 제외 후 다른 element로 재시도 (최대 3회). 초과 시 press_back (first screen이면 tap) |
| 외부 앱 감지 | press_back → package 확인 → 필요 시 target app 재실행 |
| Empty UI tree | press_back (first screen이면 tap) |
| TCP 연결 끊김 | 3회 재연결 시도 (2초 간격) |

---

## 5. TCP Protocol Specification

### 5.1 App → Server

| Header | Format | Description |
|--------|--------|-------------|
| `P` | `P` + `{package}\n` | 타겟 패키지명 전송 (연결 직후 1회) |
| `S` | `S` + `{size}\n` + `[JPEG bytes]` | Screenshot (JPEG 90% quality) |
| `X` | `X` + `{top_pkg}\n` + `{target_pkg}\n` + `{is_first("0"/"1")}\n` + `{size}\n` + `[XML bytes]` | UI hierarchy XML + 메타데이터 |
| `E` | `E` + `{json}\n` | 외부 앱 감지 알림 |
| `N` | `N` | 화면 변화 없음 신호 |
| `F` | `F` | 세션 종료 |

- 모든 텍스트 라인은 `\n`으로 종료
- 바이너리 데이터는 size-prefixed (size는 텍스트 라인으로 전송)
- Header는 single byte (ASCII)

### 5.2 Server → App

| Format | Description |
|--------|-------------|
| `{action_json}\r\n` | 실행할 action 명령 (JSON) |

Action JSON 예시:
```json
{"action_type": "tap", "x": 540, "y": 960, "element_index": 5}
```

---

## 6. Data Pipeline

### 6.1 Raw Session Structure

```
data/raw/{session_id}/
├── metadata.json           # 세션 메타데이터 (package, timestamps, step_count)
├── screenshots/            # 전환 감지된 step의 스크린샷
│   ├── 0000.png
│   └── ...
├── xml/                    # 전환 감지된 step의 UI hierarchy XML
│   ├── 0000.xml
│   └── ...
└── events.jsonl            # 전체 action 로그 (step별 JSON line)
```

### 6.2 JSONL Conversion

**Pipeline**: Raw session → ShareGPT format JSONL

연속 step 쌍 (i, i+1)에서 학습 예제를 생성한다:

| Role | Content |
|------|---------|
| system | "You are a mobile UI transition predictor..." |
| human | `<image>` + current XML (HTML-encoded) + action JSON |
| gpt | next XML (HTML-encoded) |

**XML encoding pipeline**:
```
raw XML → reformat_xml() → simplify_structure() → parse_to_html_xml()
       → encode_to_html_xml() → remove_redundancies() → indent_xml()
```

**Action mapping**: Collector의 action type을 GUI-Model format으로 변환
- `tap` → `Click`
- `swipe` → `Swipe` (방향: Up/Down/Left/Right)
- `input_text` → `Input`
- `press_back` → `Back`
- `long_press` → `LongClick`
- `press_home` → `Home`

**Output**: `gui-model_stage1.jsonl`

---

## 7. Key Design Decisions

### Client-Side Transition Detection

- **Why**: MediaProjection 100px 캡처 (<1ms) vs ADB screencap (300~800ms)
- **How**: ScreenStabilizer가 저해상도 프레임을 캡처하고, BitmapComparator가 픽셀 단위 diff 계산
- **Benefit**: 실제 전환이 발생한 경우에만 전송 → 네트워크 트래픽 및 서버 처리 최소화

### Element Exclusion on No-Change

- **Why**: 반응 없는 element (장식용, 비활성화 등)에 대한 무한 재시도 방지
- **How**: `SmartExplorer._excluded_elements`가 element index를 추적. 화면 전환 성공 또는 max retry 후 초기화
- **Benefit**: UI element의 효율적 탐색

### First Screen Protection

- **Why**: 첫 화면에서 `press_back` 실행 시 앱 종료 → 수집 세션 중단
- **How**: First frame을 기준으로 저장, 5% threshold로 비교 (시계/배지 허용). `press_back` 가중치를 0으로 설정, `tap`으로 대체
- **Benefit**: 장시간 안정적 수집 세션 유지

### Auto-Detect Target App

- **Why**: 수동 패키지명 입력은 오류가 발생하기 쉽고 개발자 지식이 필요
- **How**: FloatingCollectorButton이 START 클릭 시 AccessibilityService windows API로 foreground 앱의 package name을 자동 획득
- **Benefit**: 사용자는 타겟 앱을 열고 ▶ 버튼만 누르면 됨

### TCP Binary Protocol

- **Why**: 스크린샷, XML 등 대용량 페이로드의 효율적 전송
- **How**: Single-byte header + text metadata lines + size-prefixed binary
- **Benefit**: 직렬화 오버헤드 없음, 구현이 단순하며, 스트리밍 지원 가능
