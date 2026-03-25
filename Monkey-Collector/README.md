# Monkey-Collector

Android GUI World Modeling 학습 데이터 수집 파이프라인.

Android App (AccessibilityService) + Python Server (TCP) 아키텍처로 UI 상태 전이 데이터를 자동 수집하고, `gui-model_stage1.jsonl` 포맷으로 변환한다.

## 아키텍처

```
App (Kotlin, AccessibilityService)       TCP        Server (Python)
├── CollectorService                 ──S(screenshot)──→  server.py
│   ├── AccessibilityEvent 감지      ──X(XML)─────────→    ├── 데이터 수신
│   ├── ScreenStabilizer             ──E(external)────→    ├── storage.py (저장)
│   │   └── BitmapComparator         ──F(finish)──────→    └── wait_for_xml()
│   ├── XmlDumper (A11y tree)                                    ↓
│   └── ScreenCapture (MediaProj.)                         collector.py
├── TcpClient                        ←──action JSON────    ├── explorer.py (action 선택)
└── MainActivity (설정 UI)                                 └── adb.py (action 실행)
```

### 핵심 설계 결정

**Transition Detection → Client(App)** 에서 수행:
- MediaProjection low-res 캡처 (<1ms) vs ADB screencap (300-800ms)
- BitmapComparator: 픽셀 비교로 3프레임 연속 안정 확인 (~1.5초)
- 전환 감지 시만 Server로 전송 → 네트워크 트래픽 최소화

## 프로젝트 구조

```
Monkey-Collector/
├── app/                                   # Android App (Kotlin)
│   └── app/src/main/
│       ├── java/com/monkey/collector/
│       │   ├── CollectorService.kt            # AccessibilityService 핵심
│       │   ├── TcpClient.kt                   # TCP 전송 (S/X/E/F)
│       │   ├── ScreenStabilizer.kt            # 화면 안정화 + 전환 감지
│       │   ├── BitmapComparator.kt            # 픽셀 비교
│       │   ├── ScreenCapture.kt               # MediaProjection 스크린샷
│       │   ├── XmlDumper.kt                   # AccessibilityNodeInfo → XML
│       │   ├── MediaProjectionHelper.kt       # MediaProjection 권한 관리
│       │   └── MainActivity.kt                # 설정 UI (IP, Port, Package)
│       └── res/
│           ├── values/strings.xml
│           └── xml/accessibility_config.xml
│
├── server/                                # Python Server
│   ├── cli.py              # CLI 진입점 (run, convert, convert-all)
│   ├── server.py            # TCP 서버 (S/X/E/F 프로토콜)
│   ├── collector.py         # 메인 수집 루프 (Server 기반)
│   ├── storage.py           # DataWriter (세션 디렉토리 관리)
│   ├── explorer.py          # SmartExplorer (가중 랜덤 action 선택)
│   ├── actions.py           # Action dataclass (Tap, Swipe, Input, ...)
│   ├── adb.py               # ADB 명령어 래핑 (action 실행)
│   ├── xml_parser.py        # UIElement/UITree 파싱
│   ├── xml_encoder.py       # Raw XML → HTML-style XML 변환
│   └── converter.py         # Raw 데이터 → gui-model_stage1.jsonl 변환
│
├── pyproject.toml
└── README.md
```

## 설치

### Server (Python)

```bash
conda create -n monkey-collector python=3.11 -y
conda activate monkey-collector
pip install -e .
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
# 2) App에서 Server IP, Port, 대상 패키지 설정
# 3) Server 시작
monkey-collect run --app <package> [옵션]
```

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--app` | (필수) | 대상 앱 패키지명 |
| `--steps` | 100 | 최대 step 수 |
| `--seed` | 42 | 랜덤 시드 |
| `--delay` | 1000 | action 간 대기 (ms) |
| `--port` | 12345 | TCP 서버 포트 |
| `--output` | `data/raw` | 저장 디렉토리 |
| `--device` | - | ADB 디바이스 시리얼 |

```bash
# 예시
monkey-collect run --app com.android.calculator2 --steps 50
```

### 2. JSONL 변환

```bash
# 단일 세션 변환
monkey-collect convert \
  --session data/raw/<session_id> \
  --output ../GUI-Model/data/gui-model_stage1.jsonl \
  --images-dir ../GUI-Model/images/

# 전체 세션 일괄 변환
monkey-collect convert-all \
  --raw-dir data/raw \
  --output ../GUI-Model/data/gui-model_stage1.jsonl \
  --images-dir ../GUI-Model/images/
```

## 데이터 수집 흐름

```
App 측:
  ① AccessibilityEvent 감지 (WINDOW_STATE/CONTENT_CHANGED)
  ② ScreenStabilizer: low-res 캡처 (100px) → BitmapComparator 비교
  ③ 3프레임 연속 안정 확인 → 의미 있는 전환인지 판정
  ④ 전환 시: 고해상도 screenshot + XML dump → TCP 전송

Server 측:
  ① server.wait_for_xml() → App에서 screenshot + XML 수신
  ② xml_parser.UITree → 파싱 (clickable, scrollable, editable 추출)
  ③ SmartExplorer: 가중 랜덤 action 선택
     tap: 60% | swipe: 10% | input: 10% | back: 10% | long_press: 5%
  ④ ADB로 action 실행
  ⑤ storage에 screenshot / XML / event 저장
```

## TCP 프로토콜

### App → Server

| Header | Format | 설명 |
|--------|--------|------|
| `S` | `S` + `{size}\n` + `[PNG bytes]` | Screenshot |
| `X` | `X` + `{top_pkg}\n` + `{target_pkg}\n` + `{size}\n` + `[XML bytes]` | UI hierarchy |
| `E` | `E` + `{json}\n` | External app 감지 |
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
└── events.jsonl            # 전체 action 로그
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

## 의존성

- Python >= 3.10
- loguru >= 0.7
- Pillow >= 10.0
- Android SDK (ADB)
- Android 디바이스/에뮬레이터 (API 33+)
