# Monkey-Collector

Android GUI World Modeling 학습 데이터 수집 파이프라인.

Android App (AccessibilityService) + Python Server (TCP) 아키텍처로 UI 상태 전이 데이터를 자동 수집하고, `gui-model_stage1.jsonl` 포맷으로 변환한다.

## 아키텍처

```
App (Kotlin, AccessibilityService)       TCP        Server (Python)
├── CollectorService                 ──P(package)────→  server.py
│   ├── AccessibilityEvent 감지      ──S(screenshot)──→    ├── 데이터 수신
│   ├── ScreenStabilizer             ──X(XML+Activity)─→    ├── storage.py (저장)
│   │   └── BitmapComparator         ──E(external)────→    └── wait_for_xml()
│   ├── XmlDumper (A11y tree)        ──N(no-change)───→          ↓
│   ├── ScreenCapture (MediaProj.)   ──F(finish)──────→    collector.py
│   └── FloatingCollectorButton                            ├── explorer.py (action 선택 + 앱 복귀)
├── TcpClient                        ←──action JSON────    └── adb.py (action 실행)
└── MainActivity (설정 UI)
```

### 핵심 설계 결정

**Transition Detection → Client(App)** 에서 수행:
- MediaProjection low-res 캡처 (<1ms) vs ADB screencap (300-800ms)
- BitmapComparator: 픽셀 비교로 5프레임 연속 안정 확인 (~2.0초, 500ms 초기 대기 포함)
- 전환 감지 시만 Server로 전송 → 네트워크 트래픽 최소화

**First Screen Protection** — 첫 화면 보호:
- 첫 화면에서 `press_back` 비활성화, 대신 `tap` 실행
- ScreenStabilizer가 첫 화면을 저장하고 5% 임계값으로 비교

**No-Change Retry + Element Exclusion** — 화면 변화 없음 재시도:
- 화면 변화 없으면 해당 element를 제외하고 다른 element로 최대 3회 재시도

**External App Recovery** — 타겟 앱 이탈 복구:
- Client: 런처 감지(`LAUNCHER_PACKAGES`) 또는 back 3회 연속 시 `getLaunchIntentForPackage()`로 재실행
- Server: `external_app` signal 수신 시 능동 복구 — `return_to_app` (1-3회) → `recover` (4+회) → 세션 종료 (10+회)

## 프로젝트 구조

```
Monkey-Collector/
├── app/                                   # Android App (Kotlin)
│   └── app/src/main/
│       ├── java/com/monkey/collector/
│       │   ├── CollectorService.kt            # AccessibilityService 핵심
│       │   ├── TcpClient.kt                   # TCP 전송 (P/S/X/E/N/F)
│       │   ├── ScreenStabilizer.kt            # 화면 안정화 + 전환 감지
│       │   ├── BitmapComparator.kt            # 픽셀 비교
│       │   ├── ScreenCapture.kt               # MediaProjection 스크린샷
│       │   ├── XmlDumper.kt                   # AccessibilityNodeInfo → XML
│       │   ├── FloatingCollectorButton.kt     # 플로팅 START/STOP 버튼 (타겟 앱 자동 감지)
│       │   ├── MediaProjectionHelper.kt       # MediaProjection 권한 관리
│       │   └── MainActivity.kt                # 설정 UI (IP, Port)
│       └── res/
│           ├── values/strings.xml
│           └── xml/accessibility_config.xml
│
├── server/                                # Python Server
│   ├── cli.py              # CLI 진입점 (run, convert, convert-all)
│   ├── server.py            # TCP 서버 (P/S/X/E/N/F 프로토콜)
│   ├── collector.py         # 메인 수집 루프 (Server 기반)
│   ├── storage.py           # DataWriter (세션 디렉토리 관리)
│   ├── explorer.py          # SmartExplorer (가중 랜덤 action 선택)
│   ├── text_generator.py    # InputText 생성 전략 (LLM / 랜덤)
│   ├── activity_coverage.py # Activity 커버리지 추적 (CSV)
│   ├── cost_tracker.py      # LLM API 비용 추적 (CSV)
│   ├── actions.py           # Action dataclass (Tap, Swipe, Input, ...)
│   ├── adb.py               # ADB 명령어 래핑 (action 실행)
│   ├── xml_parser.py        # UIElement/UITree 파싱
│   ├── xml_encoder.py       # Raw XML → HTML-style XML 변환
│   └── converter.py         # Raw 데이터 → gui-model_stage1.jsonl 변환
│
├── data/
│   └── raw/                               # 수집된 세션 데이터
│
├── .env.example                           # 환경 변수 템플릿
├── pyproject.toml
└── README.md
```

## 설치

### Server (Python)

```bash
conda create -n monkey-collector python=3.11 -y
conda activate monkey-collector
pip install -e .

# LLM 기반 텍스트 입력 사용 시 환경 변수 설정
cp .env.example .env
# .env 파일에 OPENAI_API_KEY 설정
```

**요구사항**: ADB가 PATH에 있거나 `ANDROID_HOME` 환경변수 설정 필요.

### App (Android)

```bash
cd app
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

설치 후 디바이스에서 **설정 > 접근성 > Monkey Collector** 활성화 필요.

## 사용법

### 1. 데이터 수집

```bash
# 1) App 설치 및 AccessibilityService 활성화
# 2) App에서 Server IP, Port 설정 (타겟 앱은 플로팅 버튼 클릭 시 자동 감지)
# 3) Server 시작
monkey-collect run --app <package> [옵션]
```

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--app` | - | 대상 앱 패키지명 (미지정 시 클라이언트가 자동 감지) |
| `--steps` | 100 | 최대 step 수 (세션 단위) |
| `--seed` | 42 | 랜덤 시드 |
| `--delay` | 1000 | action 간 대기 (ms) |
| `--port` | 12345 | TCP 서버 포트 |
| `--output` | `data/raw` | 저장 디렉토리 |
| `--device` | - | ADB 디바이스 시리얼 |
| `--input-mode` | `api` | 텍스트 입력 모드: `api` (LLM) / `random` (하드코딩) |
| `--single` | - | 단일 세션 모드: 1회 수집 후 서버 종료 (기본: 다중 세션) |

```bash
# 기본 (다중 세션 — 여러 앱 연속 수집, Ctrl+C로 종료)
monkey-collect run --steps 100
# → 앱에서 ■ 버튼으로 세션 종료 → 다른 앱 열기 → ▶ 버튼 → 새 세션 자동 시작

# 단일 세션 (1회 수집 후 서버 자동 종료)
monkey-collect run --single --app com.android.calculator2 --steps 50
```

### 2. JSONL 변환

```bash
# 단일 세션 변환
monkey-collect convert \
  --session data/raw/<session_id> \
  --output ./data/gui-model_stage1.jsonl \
  --images-dir ./data/images/

# 전체 세션 일괄 변환
monkey-collect convert-all \
  --raw-dir data/raw \
  --output ./data/gui-model_stage1.jsonl \
  --images-dir ./data/images/
```

## 데이터 수집 흐름

```
App 측:
  ① AccessibilityEvent 감지 (WINDOW_STATE/CONTENT_CHANGED)
  ② ScreenStabilizer: low-res 캡처 (100px) → BitmapComparator 비교
  ③ 500ms 초기 대기 후 5프레임 연속 안정 확인 (~2.0초) → 의미 있는 전환인지 판정
  ③-1 전환 없음 시: N 신호 전송 → Server에서 다른 element로 재시도
  ④ 전환 시: 고해상도 screenshot + XML dump → TCP 전송 (Activity명 + first screen 여부 플래그 포함)

Server 측:
  ① server.get_latest_signal() → App에서 screenshot + XML 수신 (stale signal 드레인)
  ② xml_parser.UITree → 파싱 (clickable, scrollable, editable 추출)
  ③ SmartExplorer: 가중 랜덤 action 선택
     tap: 60% | swipe: 10% | input: 10% | back: 10% | long_press: 5% | home: 0%
     (기본 가중치, 정규화 후 적용)
     ※ 첫 화면에서는 press_back 비활성화
  ④ ADB로 action 실행 → clear_signal_queue()
  ⑤ storage에 screenshot / XML / event 저장
  ⑥ no-change 시: element 제외 후 재시도 (최대 3회), 초과 시 first screen이면 tap, 아니면 back
  ⑦ external_app 시: return_to_app (1-3회) → recover (4+회) → 세션 종료 (10+회)
```

## TCP 프로토콜

### App → Server

| Header | Format | 설명 |
|--------|--------|------|
| `P` | `P` + `{package}\n` | 타겟 패키지명 |
| `S` | `S` + `{size}\n` + `[JPEG bytes]` | Screenshot (JPEG 90%) |
| `X` | `X` + `{top_pkg}\n` + `{activity_name}\n` + `{target_pkg}\n` + `{is_first("0"/"1")}\n` + `{size}\n` + `[XML bytes]` | UI hierarchy + 메타데이터 (Activity명 포함) |
| `E` | `E` + `{json}\n` | External app 감지 |
| `N` | `N` | 화면 변화 없음 |
| `F` | `F` | 세션 종료 |

### Server → App

| Format | 설명 |
|--------|------|
| `{action_json}\r\n` | 실행할 action 명령 |

## 출력 데이터

### Raw 세션 데이터

```
data/raw/<session_id>/
├── metadata.json           # 세션 메타데이터
├── screenshots/            # 전환 감지된 step의 스크린샷
│   ├── 0000.png
│   ├── 0001.png
│   └── ...
├── xml/                    # 전환 감지된 step의 UI hierarchy XML
│   ├── 0000.xml
│   ├── 0001.xml
│   └── ...
├── events.jsonl            # 전체 action 로그
├── activity_coverage.csv   # Activity 커버리지 (step별 방문 Activity, 누적 커버리지)
└── cost.csv                # LLM API 비용 (step별 토큰 사용량, 누적 비용 USD)
```

### gui-model_stage1.jsonl (World Modeling)

현재 UI 상태 + Action → 다음 UI 상태 예측을 위한 학습 데이터:

```json
{
  "messages": [
    {
      "from": "system",
      "value": "You are a mobile UI transition predictor.\nGiven the current screen represented as html-style XML and an action description, predict the next screen's html-style XML after the action is executed."
    },
    {
      "from": "human",
      "value": "<image>\n## Current State\n<div index=\"0\">\n  <button text=\"Search\" index=\"1\" />\n</div>\n\n## Action\n{\n  \"type\": \"Click\",\n  \"params\": {},\n  \"default\": true,\n  \"index\": 1\n}"
    },
    {
      "from": "gpt",
      "value": "<div index=\"0\">\n  <input id=\"search_input\" index=\"1\" />\n  <p index=\"2\">Recent searches</p>\n</div>"
    }
  ],
  "images": ["GUI-Model/images/1_step_0001.png"]
}
```

### Action 타입

| Type | Params | 빈도 |
|------|--------|------|
| `Click` | `{}` | ~85% |
| `Input` | `{"text": "..."}` | ~11% |
| `Swipe` | `{"direction": "Up\|Down\|Left\|Right"}` | ~4% |
| `Back` | `{}` | - |
| `LongClick` | `{}` | - |
| `Home` | `{}` | rare |

## 의존성

- Python >= 3.10
- loguru >= 0.7
- Pillow >= 10.0
- openai >= 1.0
- python-dotenv >= 1.0
- Android SDK (ADB)
- Android 디바이스/에뮬레이터 (API 28+, minSdk 28)
