# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Monkey-Collector는 GUI world model 학습용 Android UI 데이터 수집 파이프라인이다. Android AccessibilityService 앱이 UI 상태 전환을 감지하여 스크린샷과 XML 계층구조를 TCP로 Python 서버에 전송하고, 서버가 SmartExplorer로 다음 액션을 선택하여 ADB로 실행하는 루프 구조.

## Build & Run

```bash
# Python 패키지 설치
pip install -e .

# 서버 시작 (클라이언트에서 타겟 앱 선택)
monkey-collect run --steps 100 --port 12345

# 또는 서버에서 타겟 앱 지정
monkey-collect run --app <package> --steps 100

# 단일 세션 변환 (raw → JSONL)
monkey-collect convert --session <dir> --output <path> --images-dir <dir>

# 전체 세션 일괄 변환
monkey-collect convert-all --raw-dir <dir> --output <path> --images-dir <dir>
```

Android 앱은 `app/` 디렉토리에서 Gradle로 빌드 (compileSdk 34, minSdk 28).

### 수집 플로우
1. 터미널: `monkey-collect run` (서버 대기)
2. 에뮬레이터: MonkeyCollector 앱 → 설정(IP/Port/Package) → Save & Ready → 권한 허용
3. 에뮬레이터: 타겟 앱 열기 → 플로팅 ▶ 버튼 클릭 → 수집 시작

## Architecture

```
Android App (Kotlin)  ←TCP→  Python Server
  CollectorService           CollectionServer
  ScreenStabilizer           Collector (orchestration loop)
  TcpClient (S/X/E/F)       SmartExplorer (weighted action selection)
                             AdbClient (action execution)
                             DataWriter (session storage)
                             Converter (raw → ShareGPT JSONL)
```

**수집 루프**: App이 A11y 이벤트 감지 → ScreenStabilizer로 전환 확정 → 스크린샷+XML을 서버로 전송 → SmartExplorer가 액션 선택 → ADB로 실행 → 저장 → 반복

**TCP 프로토콜 (App→Server)**: `P`(타겟 패키지명), `S`(스크린샷 JPEG), `X`(XML+패키지 정보), `E`(외부 앱 감지), `F`(종료). 바이너리 데이터는 크기 prefixed.

## Key Modules

| Module | Role |
|--------|------|
| `server/collector.py` | 메인 수집 오케스트레이션 루프 |
| `server/server.py` | TCP 서버, 바이너리 프로토콜 파싱 |
| `server/explorer.py` | 가중치 기반 랜덤 액션 선택 (tap 60%, swipe/back/input 10%, long_press 5%) |
| `server/xml_parser.py` | uiautomator XML → UIElement/UITree 파싱 |
| `server/xml_encoder.py` | XML → HTML-style 변환 파이프라인 (reformat→simplify→remove bounds→encode) |
| `server/converter.py` | raw 세션 → ShareGPT JSONL 변환 |
| `server/adb.py` | ADB 커맨드 래퍼 (자동 SDK 경로 탐색) |
| `server/storage.py` | 세션별 디렉토리 구조 관리 (`data/raw/{session_id}/`) |
| `server/actions.py` | Action 데이터클래스 (Tap, Swipe, InputText, LongPress, PressBack, PressHome) |

## Android App (Kotlin)

`app/app/src/main/java/com/monkey/collector/` 하위:

- **CollectorService**: AccessibilityService. WINDOW_STATE/CONTENT_CHANGED 이벤트 디바운스(300ms) 후 ScreenStabilizer로 전환 확정. 플로팅 버튼 관리
- **FloatingCollectorButton**: TYPE_ACCESSIBILITY_OVERLAY 플로팅 START/STOP 버튼. 타겟 앱 foreground 유지
- **ScreenStabilizer**: 저해상도(100px) 프레임 비교로 안정화 감지 (2% 임계값, 3연속 안정 프레임)
- **TcpClient**: P/S/X/E/F 프로토콜 구현, synchronized write, 3회 재시도
- **MainActivity**: 설정(IP/Port/Package) 저장 + MediaProjection 권한 요청 전용

## Data Format

**Raw 세션 구조**:
```
data/raw/{session_id}/
├── metadata.json
├── screenshots/0000.png, 0001.png, ...
├── xml/0000.xml, 0001.xml, ...
└── events.jsonl
```

**변환 출력**: ShareGPT 형식 JSONL. 연속 XML 쌍(before/after) + 액션으로 학습 예제 생성.

## Dependencies

Python: `loguru`, `Pillow` (그 외 ADB/TCP/XML은 stdlib). Python ≥3.10 필요.
