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
│    ├─ XmlDumper                          │      │    │   └─ TextGenerator (LLM/Random)   │
│    └─ TcpClient (P/S/X/E/N/F)           │      │    ├─ AdbClient (action execution)     │
│                                          │      │    ├─ DataWriter (session storage)     │
│                                          │      │    ├─ PageGraph (page map builder)     │
│                                          │      │    ├─ GraphVisualizer (PyVis HTML)     │
│                                          │      │    ├─ ActivityCoverageTracker (CSV)    │
│                                          │      │    └─ CostTracker (CSV)               │
│                                          │      │                                       │
│  MediaProjectionHelper (singleton)       │      │  Converter (raw → JSONL)               │
└──────────────────────────────────────────┘      │  xml_parser, parser/ (StructuredXmlParser) │
                                                  └───────────────────────────────────────┘
```

### 수집 루프 흐름

```
App: A11y 이벤트 감지 (debounce 300ms)
  → ScreenStabilizer: 화면 안정화 대기
  → hasVisualChange(): 실제 변화 여부 판별
  → 변화 없음 → N signal 전송 → Server: 다른 element로 재시도
  → 외부 앱 감지 → E signal 전송 → back 또는 강제 재실행
  → 변화 있음 → Screenshot + XML 캡처 → TCP 전송
  → Server: get_latest_signal()로 stale signal 드레인 후 최신 signal 처리
  → parse → select action → ADB 실행 → clear_signal_queue() → 저장
  → 반복
```

---

## 3. Android App

### 3.1 Components

#### CollectorService (`CollectorService.kt`)

AccessibilityService 핵심 서비스. Singleton pattern (`instance` property)으로 관리되며, `WINDOW_STATE_CHANGED`와 `WINDOW_CONTENT_CHANGED` 이벤트를 수신하고 300ms debounce로 중복 이벤트를 필터링한다.

**Activity 추적**: `TYPE_WINDOW_STATE_CHANGED` 이벤트에서 `event.packageName + "/" + event.className`으로 현재 Activity 클래스명을 추적한다 (`currentActivityName` 필드). `EXCLUDED_PACKAGES` 및 `android.widget.*` 클래스는 필터링. X 메시지 전송 시 현재 Activity명을 포함하여 서버 측 Activity 커버리지 추적에 사용.

이벤트 처리 사이클:
1. Activity 추적 (`TYPE_WINDOW_STATE_CHANGED`에서 className 캡처, isCollecting 여부와 무관)
2. 이벤트 수신 및 debounce 필터링
3. TCP 연결 상태 확인
4. `getTopInteractableRoot()`로 최상위 인터랙터블 윈도우의 root node 및 package name 획득
5. 외부 앱 감지 시: `E` signal 전송 + `performGlobalAction(BACK)`. `consecutiveBackCount`로 연속 back 횟수 추적, 3회 연속 또는 런처 감지(`LAUNCHER_PACKAGES` set + `isLauncher()`) 시 `packageManager.getLaunchIntentForPackage()`로 타겟 앱 재실행 (실패 시 `am start -a MAIN -c LAUNCHER` fallback)
6. Worker thread에서: ScreenStabilizer 안정화 대기 → 시각적 변화 검사 → 스크린샷/XML 캡처 → TCP 전송 (Activity명 포함)

`EXCLUDED_PACKAGES`로 `com.android.systemui`, `com.android.permissioncontroller`, `com.monkey.collector` 를 필터링한다.

step count, first screen flag를 관리하며, `startCollection()`/`stopCollection()`으로 수집 생명주기를 제어한다.

**Foreground Service**: API 29+ MediaProjection 사용을 위해 `startForeground()`로 foreground service를 시작한다. `FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION` 타입의 NotificationChannel을 생성한다.

#### FloatingCollectorButton (`FloatingCollectorButton.kt`)

`TYPE_ACCESSIBILITY_OVERLAY` 기반 플로팅 오버레이 버튼. AccessibilityService가 활성화되어 있으면 `SYSTEM_ALERT_WINDOW` 권한 없이 사용 가능하며, accessibility window 목록에 포함되지 않아 `getTopInteractableRoot()`에 간섭하지 않는다.

- **크기**: 48dp, 원형
- **상태**: START(▶ 초록) / STOP(■ 빨강)
- **드래그**: touch 이동 지원, click 판별 threshold 10px
- **타겟 앱 자동 감지**: START 클릭 시 `service.getCurrentForegroundPackage()`로 foreground 앱의 package name을 자동 획득
- **스크린샷 보호**: 캡처 중 `dismiss()` → 캡처 후 `show()`로 스크린샷에 버튼이 포함되지 않도록 처리

#### ScreenStabilizer (`ScreenStabilizer.kt`)

MediaProjection VirtualDisplay + ImageReader를 사용하여 저해상도(100px 너비) 프레임을 캡처하고, 연속 프레임 간 픽셀 비교로 화면 안정화 및 전환을 감지한다.

**Capture Session 분리**: `startCaptureSession()`으로 VirtualDisplay/ImageReader를 생성하고, `stopCaptureSession()`으로 해제한다. MediaProjection 자체는 유지하여 재사용하며, `release()`에서만 완전 해제한다. 기존 MediaProjection이 있으면 새로 생성하지 않는 최적화가 적용되어 있다.

**안정화 감지** (`waitForStable()`):
- 1000ms 초기 대기로 애니메이션 시작을 보장
- 300ms 간격으로 저해상도 프레임 캡처
- BitmapComparator로 연속 프레임 비교
- 7개 연속 프레임이 1.5% 미만 차이일 때 안정화 판정
- **Oscillation 감지**: 프레임 해시(8×8 grid 평균 휘도) ring buffer(10프레임)로 주기 1~3의 반복 패턴(3회 이상) 감지 → 커서 깜빡임, 미세 애니메이션 등에서 조기 안정화 판정
- 최대 60회 시도 (약 19초, 초기 대기 포함), 타임아웃 시 현재 프레임을 `lastStableFrame`으로 저장
- Atomic flag로 동시 안정화 시도 방지

**시각적 변화 감지** (`hasVisualChange()`):
- 현재 프레임과 `lastStableFrame` 비교
- 안정화 성공 시 1.5% threshold, 타임아웃 시 5% threshold (adaptive) — 미세 애니메이션 오탐 방지
- 변화 없으면 `false` 반환 → N signal 전송으로 이어짐

**First screen 감지**:
- `saveFirstScreen()`: step 0에서 호출, 기준 프레임 저장
- `isFirstScreen()`: 현재 프레임과 저장된 first screen 비교 (5% threshold, 시계/배지 등 동적 콘텐츠 허용)

#### BitmapComparator (`BitmapComparator.kt`)

두 Bitmap의 픽셀 단위 RGBA 비교. 전체 픽셀 중 차이가 있는 픽셀의 비율을 0.0~1.0으로 반환한다. `computeFrameHash()`: 비트맵을 8×8 그리드로 나눠 셀별 평균 휘도를 계산하여 64바이트 perceptual hash를 생성 (oscillation 감지용).

#### ScreenCapture (`ScreenCapture.kt`)

API 30+ `AccessibilityService.takeScreenshot()` 래퍼. `HardwareBuffer`에서 `Bitmap`으로 변환한다. `CountDownLatch`를 사용한 동기 캡처 (5초 타임아웃).

#### XmlDumper (`XmlDumper.kt`)

`AccessibilityNodeInfo` 트리를 순회하여 uiautomator 호환 XML을 생성한다.
- 텍스트 정리: 줄바꿈 → 공백, XML escape 처리
- 14개 boolean attribute + bounds + text + resource-id + class + package 캡처

#### TcpClient (`TcpClient.kt`)

양방향 TCP 통신 구현체.
- **App→Server**: P/S/X/E/N/F 프로토콜. X 메시지: top_pkg, activity_name, target_pkg, is_first_screen, xml_data. 스크린샷: JPEG 90% quality 압축. Thread safety: `synchronized(writeLock)`으로 모든 write 직렬화
- **Server→App**: reader 스레드에서 `\r\n` 구분 JSON 수신. `{"type":"SESSION_END"}` → `onSessionEnd` 콜백 호출 → CollectorService가 `stopCollection()` 실행. 소켓 닫힘 시 자동 종료
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
| TCP reader | TcpClient reader 스레드: 서버 제어 신호(SESSION_END 등) 수신 |
| ScreenStabilizer | Worker thread에서 blocking 대기 (`Thread.sleep` 루프) |

### 3.3 Screen Transition Detection

```
AccessibilityEvent 수신 (debounced 300ms)
    │
    ▼
ScreenStabilizer.waitForStable()
    │  1000ms 초기 대기 (애니메이션 시작 보장)
    │  100px 프레임을 300ms 간격으로 캡처
    │  7개 연속 프레임 < 1.5% diff → 안정화 완료
    │  OR oscillation 감지 (2~3프레임 교대 반복 3회) → 조기 안정화
    │  (최대 60회 = ~19초, 타임아웃 시 lastStableFrame 업데이트)
    │
    ▼
ScreenStabilizer.hasVisualChange()
    │  현재 프레임 vs lastStableFrame
    │  (안정화 성공: 1.5%, 타임아웃: 5% adaptive threshold)
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
- **비교**: `isFirstScreen()` — 현재 프레임과 저장된 기준 프레임을 5% threshold로 비교 (시계, 알림 배지 등 동적 콘텐츠 변화를 허용하기 위해 안정화 threshold 1.5%보다 완화)
- **효과**: Server의 SmartExplorer가 first screen에서 `press_back` 가중치를 0으로 설정하고, `tap`으로 대체

---

## 4. Python Server

### 4.1 Components

#### CollectionServer (`server.py`)

TCP 서버. `0.0.0.0:12345`에서 Android 앱의 연결을 대기한다.

- 바이너리 프로토콜 파싱: single-byte header → 메타데이터 텍스트 → size-prefixed 바이너리
- `Queue` 기반 signal 전달: 4가지 signal type (`xml`, `no_change`, `external_app`, `finish`)을 큐잉
- `get_latest_signal()`: stale signal을 모두 드레인한 후 최신 signal 반환. 큐가 비어 있으면 `timeout`까지 blocking 대기
- `send_session_end()`: 앱에 `{"type":"SESSION_END"}` 전송. 세션 종료 시 `_run_session()` finally 블록에서 호출
- `reset_for_new_session()`: 다중 세션 모드에서 세션 간 상태 초기화 (`_package_event`, signal queue, XML state 등). 기존 클라이언트 소켓을 명시적으로 close하여 앱이 disconnect를 감지. 서버 소켓과 accept 루프는 유지
- `clear_signal_queue()`: 큐에 쌓인 모든 signal 폐기 (액션 실행 후 호출)
- `wait_for_package()`: `threading.Event`로 P 메시지의 package name을 blocking 대기
- `threading.Event`로 package name/XML 동기화
- Daemon thread에서 client 핸들링

#### Collector (`collector.py`)

메인 오케스트레이션. 전체 수집 세션의 생명주기를 관리한다.

- `run()`: 단일 세션 모드. 서버 시작 → 1회 수집 → 서버 종료
- `run_multi()`: 다중 세션 모드. 서버를 한 번 시작한 후 여러 세션을 연속 수집. 세션 간 `reset_for_new_session()`으로 서버 상태 초기화. Ctrl+C로 종료
- `_run_session()`: 단일 세션 실행 (서버 생명주기는 호출자가 관리). 세션 디렉토리는 패키지명으로 저장 (`data/raw/{package}/`). **같은 앱의 기존 세션을 자동 감지하여 resume** — `DataWriter.find_existing_session()`으로 검색, 있으면 `resume_session()`으로 step 이어서 시작, tracker들도 `resume()`로 CSV append. `--new-session` 플래그 시 기존 폴더 삭제 후 새 세션 생성. 세션 종료 시 전체 XML로 PageGraph 재빌드 (`build_graph_from_session()`)

루프 구조:
1. `server.get_latest_signal(timeout=25s)` — stale signal 드레인 후 최신 signal 대기
2. Signal에 따라 분기:
   - `None` (timeout): 화면 중앙 tap, 5회 연속 시 세션 종료
   - `"no_change"`: element exclusion + 재시도 (최대 3회), 초과 시 press_back (first screen이면 `_tap_random_fallback()`)
   - `"external_app"`: `return_to_app()` (1-3회) → `recover()` (4+회) → 세션 종료 (10+회). `external_app_count`는 유효한 UI tree가 있는 화면에서만 리셋
   - `"xml"`: 정상 처리 — parse → select → execute → `clear_signal_queue()` → save. 빈 UI tree인 경우 대기 후 재시도 (최대 2회), 초과 시 press_back

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
- 모든 가중치가 0일 때: first screen이면 clickable element tap 또는 랜덤 좌표 tap, 아니면 `PressBack`

**Element exclusion**: `_excluded_elements` set으로 시도했지만 화면 변화가 없었던 element의 index를 추적. 화면 전환 성공 시 또는 max retry 후 초기화.

**`input_text` 동작**: editable element의 center 좌표를 먼저 tap하여 focus를 획득한 후 (0.3초 대기), TextGenerator가 텍스트를 생성한다. `--input-mode api` (기본값): OpenAI gpt-5-nano (Responses API, reasoning: minimal, verbosity: low)가 화면 HTML XML + 타겟 필드 정보를 분석하여 맥락에 맞는 텍스트 생성. `--input-mode random`: 기존 `SAMPLE_TEXTS` 10개 중 랜덤 선택. API 실패 시 자동으로 랜덤 fallback.

**주요 메서드**:
- `select_action()`: UI tree 분석 후 가중치 기반 액션 선택
- `execute_action()`: Action 인스턴스를 ADB 명령으로 실행
- `has_left_app()`: 현재 패키지가 타겟 앱인지 확인
- `return_to_app()`: back → 패키지 확인 → 필요 시 launch_app
- `recover()`: 에러 상태 복구 (home → launch_app)

#### AdbClient (`adb.py`)

ADB 명령어 래퍼. `PATH`, `ANDROID_HOME`, `~/Library/Android/sdk` 순서로 자동 탐색.

주요 메서드: `tap`, `swipe`, `input_text` (특수 문자 escaping), `long_press`, `press_back`, `press_home`, `launch_app`, `get_current_package`, `get_current_activity`, `get_declared_activities`, `get_device_resolution`

#### DataWriter (`storage.py`)

세션별 디렉토리 구조 관리. incremental step counter로 파일 네이밍. `save_xml()`은 raw XML과 함께 4종의 파싱된 XML 변형을 자동 생성한다.

**세션 이어서 저장 (Resume)**: 세션 디렉토리는 패키지명으로 저장. 같은 앱을 다시 수집하면 항상 기존 세션에 이어서 저장한다.
- `find_existing_session(package)`: `data/raw/{package}/` 디렉토리 존재 여부로 기존 세션 검색
- `resume_session(session_id)`: XML 파일 수로 `step_count` 복원, `resumed_at` 타임스탬프 기록
- `init_session()`: 기존과 동일, 새 세션 생성 시 사용

구조:
```
data/raw/{package}/
├── metadata.json
├── screenshots/
├── xml/
│   ├── 0000.xml                # raw uiautomator dump
│   ├── 0000_parsed.xml         # semantic HTML tags + bounds + index
│   ├── 0000_hierarchy.xml      # 구조만 (text/bounds/index 제거)
│   ├── 0000_encoded.xml        # bounds 제거, index만 (LLM 입력용)
│   └── 0000_pretty.xml         # encoded의 pretty-print
├── events.jsonl
├── activity_coverage.csv
└── cost.csv
```

metadata.json 필드: `session_id`, `package`, `started_at`, `completed_at`, `total_steps`, `external_app_events`, `resumed_at` (타임스탬프 배열, resume 시 추가).

`log_external_app()`: 외부 앱 감지 이벤트를 events.jsonl에 기록하고 metadata의 `external_app_events` 카운터를 증가시킨다.

#### UITree / UIElement (`xml_parser.py`)

uiautomator XML을 flat list로 파싱. `UIElement`는 17개 attribute를 가진다: `index`, `resource_id`, `class_name`, `text`, `content_desc`, `bounds`, `clickable`, `scrollable`, `enabled`, `checkable`, `checked`, `long_clickable`, `password`, `selected`, `package`, `visible`, `important`. Computed properties: `area`, `center`, `short_class`, `display_name`.

`EDITABLE_CLASSES` 상수로 EditText 계열 클래스를 정의한다: `EditText`, `AutoCompleteTextView`, `MultiAutoCompleteTextView`, `ExtractEditText`, `SearchAutoComplete`.

쿼리 메서드:
- `get_clickable_elements()`: clickable + enabled + area > 0
- `get_editable_elements()`: EDITABLE_CLASSES 매칭 + enabled
- `get_scrollable_elements()`: scrollable
- `get_interactable_elements()`: clickable/scrollable/long_clickable/checkable/editable 중 하나 이상

Factory: `UITree.from_xml_string(xml_str)` — XML 문자열에서 직접 UITree 생성

#### StructuredXmlParser (`server/parser/`)

raw XML → LLM 친화적 HTML-style XML 변환 파이프라인. 3파일 구성: `base.py` (추상 Parser), `structured_parser.py` (구현체), `__init__.py`.

**5단계 파이프라인** (`parse()`):
1. **_reformat**: 시맨틱 태그 변환. Android 클래스 → 중간 태그(Button, TextField, Image, Scroll, Checker). `resource-id` → `id` (패키지 prefix 제거). `content-desc` → `description`. 빈 leaf node 가지치기
2. **_simplify**: 반복적 구조 축소. meaningless leaf 제거 + 단일 자식 wrapper 축소 (수렴까지 반복). Button/Checker 보존
3. **_clean**: HTML 태그 정규화 (Button→button, TextField→p, Image→img, Scroll→div[data-scroll], Checker→input[type=checkbox]). `[0,0][0,0]` bounds 제거. attribute whitelist 적용 (tag별 허용 속성). scroll 컨테이너 내 중복 자식 제거 (bounds/index 제외한 구조 비교)
4. **_renumber**: 순차 인덱스 재할당 (pre-order traversal)
5. **pretty_xml**: XML 들여쓰기

**bounds 관리**: `_clear_bounds()` — bounds를 `bounds_cache`에 저장 후 XML에서 제거. `get_bounds(index)` / `find_element_by_index()` / `find_element_by_bounds()`로 조회.

**편의 함수**: `parse_to_html_xml()` (bounds 포함), `encode_to_html_xml()` (bounds 제거), `hierarchy_parse()` (구조만), `indent_xml()` (정렬)

#### Converter (`converter.py`)

Raw 세션 → ShareGPT 형식 JSONL 변환.

연속 XML 쌍 (step i, step i+1)에서 학습 예제 생성:
- **system**: "You are a mobile UI transition predictor..."
- **human**: `<image>` + before XML (HTML-encoded) + action JSON
- **gpt**: after XML (HTML-encoded)

**Event 필터링**: `transition` 필드가 `false`인 이벤트 (no-change retry 등)는 변환에서 제외된다.

**Image naming**: `{session_label}_step_{count:04d}.png` 형식. 이미지 경로는 `GUI-Model/images/{image_name}`으로 기록된다.

**헬퍼 함수**: `_find_element_at()` — 좌표로부터 가장 작은 bounding box의 element를 검색. `_find_event_by_index()` — step index 매칭 실패 시 sequential position fallback.

출력: `gui-model_stage1.jsonl`

#### ActivityCoverageTracker (`activity_coverage.py`)

세션별 Activity 방문 커버리지를 CSV로 추적한다. `adb shell dumpsys package`로 앱의 전체 선언 Activity를 조회하고, 매 step마다 Android 앱이 TCP X 메시지로 전송한 Activity명을 기록한다 (앱이 전송하지 않는 경우 `adb shell dumpsys activity activities`로 fallback).

CSV 컬럼: `timestamp_sec`, `step`, `activity`, `unique_visited`, `total_activities`, `coverage`

- `initialize(session_dir, total_activities)`: CSV 헤더 작성, 내부 상태 초기화 (multi-session 재사용 가능)
- `resume(session_dir, total_activities)`: 기존 CSV에서 `visited_activities` 복원 후 append 모드로 동작
- `record(activity_name, step)`: visited_activities set에 추가, coverage 계산, CSV append
- `get_coverage()`: 현재 커버리지 비율 (0.0~1.0)

#### CostTracker (`cost_tracker.py`)

LLM API 호출별 토큰 사용량과 비용을 CSV로 추적한다. `MODEL_PRICING` 딕셔너리에 모델별 1M 토큰당 가격(USD)이 정의되어 있다.

CSV 컬럼: `timestamp_sec`, `step`, `agent`, `model`, `input_tokens`, `output_tokens`, `cost_usd`, `total`

- `initialize(session_dir)`: CSV 헤더 작성, 누적 비용 초기화
- `resume(session_dir)`: 기존 CSV에서 누적 비용(`_total`) 복원 후 append 모드로 동작
- `record(model, input_tokens, output_tokens, step, agent)`: 비용 계산 + CSV append
- `_calc_cost()`: MODEL_PRICING 기반 비용 계산 (input_tokens * price + output_tokens * price) / 1M

TextGenerator에서 OpenAI API 응답의 `usage` 필드를 읽어 자동 기록한다.

#### CLI (`cli.py`)

Entry point: `monkey-collect`. Subcommands: `run`, `convert`, `convert-all`, `page-map`, `page-map-all`. `run`은 기본 다중 세션 모드, `--single` 플래그로 단일 세션 모드 전환. `--new-session` 플래그로 기존 세션 데이터를 삭제하고 새 세션 강제 생성 (기본: 기존 세션에 이어서 저장).

### 4.2 Collection Loop

```python
while step < max_steps:
    # Stale signal 드레인 후 최신 signal 대기
    result = server.get_latest_signal(timeout=25s)

    if result is None:              # True timeout (signal 없음)
        timeout_count++
        if timeout_count >= 5: break   # 세션 종료
        tap(screen_center)             # 화면 활성화 시도
        continue

    if signal_type == "no_change":  # 앱이 시각적 변화 없음 보고
        exclude(last_element)       # 해당 element 제외
        retry_count++
        if retry_count >= 3:
            first_screen? → _tap_random_fallback() : press_back
            if not first_screen and has_left_app(): return_to_app()  # back으로 앱 이탈 시 복구
            clear_signal_queue()    # Stale signals 폐기
            reset counters
        else:
            select_different_action(same_ui_tree)  # 다른 element/action 시도
            log_event(no_change_retry=True)
        continue

    if signal_type == "external_app":  # Server-side 능동 복구
        external_app_count++
        if external_app_count >= MAX_EXTERNAL_APP_RETRIES(10): break  # 세션 종료
        if external_app_count <= 3:
            explorer.return_to_app(package)    # back → 확인 → 필요 시 launch
        else:
            explorer.recover(package)          # home → launch
        clear_signal_queue()
        continue

    if signal_type == "finish":     # 클라이언트 종료(■ 버튼) 또는 연결 끊김
        break                       # 즉시 세션 종료 (타임아웃 대기 없음)

    if signal_type == "xml":        # 화면 전환 발생
        reset retry/timeout/no_change counters
        clear excluded_elements
        record activity_coverage (meta.activity_name → CSV, ADB fallback)
        update text_generator step (for cost tracking)
        check app_escape (top_pkg != target_pkg → skip)
        save screenshot + xml
        parse UI tree
        if empty:
            empty_ui_retries++
            if empty_ui_retries <= 2:  # 앱 로딩 중일 수 있음
                sleep(1.0)             # 대기 후 다음 signal 기다림
                continue
            empty_ui_retries = 0
            first_screen? → _tap_random_fallback() : press_back
            if not first_screen and has_left_app(): return_to_app()
        empty_ui_retries = 0
        external_app_count = 0      # 유효 UI tree에서만 리셋
        select action (SmartExplorer)
        execute action (ADB)
        clear_signal_queue()        # 액션 실행 중 쌓인 stale signals 폐기
        save event log
```

### 4.3 Error Recovery

| 상황 | 대응 |
|------|------|
| Timeout (signal 없음) | 화면 중앙 tap으로 활성화 시도. 5회 연속 시 세션 종료 |
| 시각적 변화 없음 (N signal) | element 제외 후 다른 element로 재시도 (최대 3회). 초과 시 press_back (first screen이면 `_tap_random_fallback()`) |
| 외부 앱 감지 (Client) | `E` signal 전송 + press_back → `consecutiveBackCount` 추적 → 3회 연속 또는 런처 감지 시 `getLaunchIntentForPackage()` 재실행 (fallback: `am start -a MAIN -c LAUNCHER`) |
| 외부 앱 감지 (Server) | `external_app` signal 수신 → `external_app_count` 추적: 1-3회 `return_to_app()` (back → launch), 4+회 `recover()` (home → launch), 10+회 세션 종료 |
| Empty UI tree | 앱 로딩 중일 수 있으므로 대기 후 재시도 (최대 2회). 초과 시 press_back (first screen이면 `_tap_random_fallback()`) |
| TCP 연결 끊김 | 3회 재연결 시도 (2초 간격) |
| 예외 발생 | `SmartExplorer.recover()` — home → launch_app |

---

## 5. TCP Protocol Specification

### 5.1 App → Server

| Header | Format | Description |
|--------|--------|-------------|
| `P` | `P` + `{package}\n` | 타겟 패키지명 전송 (연결 직후 1회) |
| `S` | `S` + `{size}\n` + `[JPEG bytes]` | Screenshot (JPEG 90% quality) |
| `X` | `X` + `{top_pkg}\n` + `{activity_name}\n` + `{target_pkg}\n` + `{is_first("0"/"1")}\n` + `{size}\n` + `[XML bytes]` | UI hierarchy XML + 메타데이터 (Activity명 포함) |
| `E` | `E` + `{json}\n` | 외부 앱 감지 알림 |
| `N` | `N` | 화면 변화 없음 신호 |
| `F` | `F` | 세션 종료 |

- 모든 텍스트 라인은 `\n`으로 종료
- 바이너리 데이터는 size-prefixed (size는 텍스트 라인으로 전송)
- Header는 single byte (ASCII)

### 5.2 Server → App

`\r\n` 구분 JSON 메시지. TcpClient reader 스레드에서 수신.

| Format | Description |
|--------|-------------|
| `{"type":"SESSION_END"}\r\n` | 세션 종료 알림. 앱이 `stopCollection()` 실행 |
| `{action_json}\r\n` | 실행할 action 명령 (미래 확장용) |

- 세션 종료 시 서버가 `send_session_end()` 호출 → 앱의 reader 스레드가 수신 → `stopCollection()`
- 소켓 close를 fallback으로 활용: SESSION_END를 놓쳐도 소켓 닫힘으로 reader 스레드가 종료됨

---

## 6. Data Pipeline

### 6.1 Raw Session Structure

```
data/raw/{package}/
├── metadata.json           # 세션 메타데이터 (package, timestamps, step_count)
├── screenshots/            # 전환 감지된 step의 스크린샷
│   ├── 0000.png
│   └── ...
├── xml/                    # 전환 감지된 step의 UI hierarchy XML (5종)
│   ├── 0000.xml            # raw uiautomator dump
│   ├── 0000_parsed.xml     # semantic HTML tags + bounds + index
│   ├── 0000_hierarchy.xml  # 구조만 (text/bounds/index 제거)
│   ├── 0000_encoded.xml    # bounds 제거, index만 (LLM 입력용)
│   ├── 0000_pretty.xml     # encoded의 pretty-print
│   └── ...
├── events.jsonl            # 전체 action 로그 (step별 JSON line)
├── activity_coverage.csv   # Activity 커버리지 추적 (step별 방문 Activity 기록)
└── cost.csv                # LLM API 토큰 사용량 및 비용 추적
```

### 6.2 JSONL Conversion

**Pipeline**: Raw session → ShareGPT format JSONL

연속 step 쌍 (i, i+1)에서 학습 예제를 생성한다:

| Role | Content |
|------|---------|
| system | "You are a mobile UI transition predictor..." |
| human | `<image>` + current XML (HTML-encoded) + action JSON |
| gpt | next XML (HTML-encoded) |

**Event 필터링**: events.jsonl에서 `transition: false` 이벤트 (no-change retry)는 건너뛴다. encoded XML이 before/after 동일한 경우도 제외된다.

**XML encoding pipeline** (`server/parser/structured_parser.py`):
```
raw XML → _reformat() → _simplify() → _clean() → _renumber() → pretty_xml()
       → _clear_bounds() (bounds 제거) → indent_xml()
```

**Action mapping**: Collector의 action type을 GUI-Model format으로 변환
- `tap` → `Click` (element_index 우선, fallback: 좌표로 최소 bounding box element 검색)
- `swipe` → `Swipe` (방향: Up/Down/Left/Right, 좌표 차이로 판별)
- `input_text` → `Input`
- `press_back` → `Back`
- `long_press` → `LongClick`
- `press_home` → `Home`

**Image naming**: `{session_label}_step_{count:04d}.png`. 이미지 경로는 `GUI-Model/images/{image_name}`으로 JSONL에 기록.

**Output**: `gui-model_stage1.jsonl`

---

## 7. Key Design Decisions

### Client-Side Transition Detection

- **Why**: MediaProjection 100px 캡처 (<1ms) vs ADB screencap (300~800ms)
- **How**: ScreenStabilizer가 1000ms 초기 대기 후 저해상도 프레임을 300ms 간격으로 캡처하고, BitmapComparator가 픽셀 단위 diff 계산 (1.5% 임계값, 7프레임 연속 안정)
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

### LLM-based InputText Generation

- **Why**: 하드코딩 텍스트(`SAMPLE_TEXTS`)는 맥락을 무시하여 비현실적 데이터 생성 (검색창에 이메일, 메모앱에 "12345" 등)
- **How**: Strategy pattern — `TextGenerator` ABC에 `RandomTextGenerator`(기존)와 `LLMTextGenerator`(OpenAI gpt-5-nano, Responses API) 구현. `--input-mode` CLI 옵션으로 전환. LLM에 `encode_to_html_xml()` 결과 + 타겟 필드 정보 전송, `reasoning={"effort": "minimal"}`, `text={"verbosity": "low"}`로 빠르고 간결한 응답 생성
- **Benefit**: 필드 유형과 앱 맥락에 맞는 현실적 텍스트 자동 생성. API 실패 시 기존 랜덤 방식으로 자동 fallback하여 안정성 유지

### Multi-Session Support

- **Why**: 여러 앱을 연속 수집할 때 세션마다 서버를 재시작하는 번거로움 제거
- **How**: 기본 동작이 다중 세션 모드 (`Collector.run_multi()`). 서버를 유지한 채 세션 루프를 반복. 세션 간 `CollectionServer.reset_for_new_session()`으로 `_package_event`, signal queue, XML state 등을 초기화. F 시그널 및 클라이언트 연결 끊김 시 `"finish"` signal을 큐에 넣어 수집 루프가 즉시 종료. `--single` 플래그로 기존 단일 세션 모드 사용 가능
- **Benefit**: 앱에서 ■ → 다른 앱 열기 → ▶만 누르면 새 세션 시작. TCP 프로토콜 변경 없이 서버 측만 수정하여 Android 앱 수정 불필요

### Session Resume (이어서 저장)

- **Why**: 같은 앱을 여러 번 수집할 때 별도 디렉토리에 분리되면 그래프가 분절되고 데이터 관리가 어려움
- **How**: 세션 디렉토리는 패키지명으로 저장 (`data/raw/{package}/`). `_run_session()` 시작 시 `DataWriter.find_existing_session(package)`로 기존 세션 검색. 있으면 `resume_session()`으로 step_count 복원 (XML 파일 수 기반), tracker들 `resume()`으로 기존 CSV에서 상태 복원 후 append. 세션 종료 시 `build_graph_from_session()`으로 전체 XML 재빌드하여 통합 PageGraph 생성. `--new-session` 플래그로 기존 폴더 삭제 후 새 세션 강제 생성
- **Benefit**: 같은 앱의 모든 수집 데이터가 하나의 디렉토리에 통합되어 PageGraph가 완전한 탐색 커버리지를 반영

### TCP Binary Protocol

- **Why**: 스크린샷, XML 등 대용량 페이로드의 효율적 전송
- **How**: Single-byte header + text metadata lines + size-prefixed binary
- **Benefit**: 직렬화 오버헤드 없음, 구현이 단순하며, 스트리밍 지원 가능

### Page Map (UI State Graph)

- **Why**: 수집된 데이터에서 앱의 페이지 구조와 전환 관계를 자동으로 파악하기 위함. MobileGPT-V2의 subtask_graph에 대응
- **How**: 2단계 페이지 식별 — Activity name (primary) + XML 구조 fingerprint (secondary). fingerprint는 parser 전처리(_reformat+_simplify) 후 `(tag, id, depth)` 튜플 집합의 MD5 해시. 시맨틱 태그(Button, TextField, Scroll 등) 사용으로 커스텀 클래스명 변경에 강건, wrapper 축소로 depth 변화에 안정적. scrollable 자식은 3개로 제한. 같은 Activity 내에서 Jaccard 유사도 ≥ 0.85이면 같은 페이지. 전환 그래프는 `(from_page, to_page, action_type)` dedup key로 중복 필터링, self-loop 제외. 실시간(수집 중) + 사후(저장된 세션에서) 모두 빌드 가능. PyVis로 인터랙티브 HTML 시각화 (Activity별 노드 색상, 전환 빈도별 엣지 너비)
- **Benefit**: 앱 탐색 커버리지 시각적 확인, 전환 패턴 분석, world model 학습 데이터 품질 검증
