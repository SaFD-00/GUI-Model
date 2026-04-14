# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Monkey-Collector는 GUI world model 학습용 Android UI 데이터 수집 파이프라인이다. Android AccessibilityService 앱이 UI 상태 전환을 감지하여 스크린샷과 XML 계층구조를 TCP로 Python 서버에 전송하고, 서버가 SmartExplorer로 다음 액션을 선택하여 ADB로 실행하는 루프 구조.

## Build & Run

```bash
# Python 패키지 설치
pip install -e .

# 환경 변수 설정 (LLM 기반 텍스트 입력 사용 시)
cp .env.example .env
# .env 파일에 OPENAI_API_KEY 설정

# 서버 시작 (기본: 다중 세션 모드, Ctrl+C로 종료)
monkey-collect run --steps 100 --port 12345

# 서버에서 타겟 앱 지정
monkey-collect run --app <package> --steps 100

# 단일 세션 모드 (1회 수집 후 서버 자동 종료)
monkey-collect run --single --steps 100

# 같은 앱에 기존 세션이 있어도 새 세션 디렉토리 강제 생성
monkey-collect run --steps 100 --new-session

# 하드코딩 랜덤 텍스트 사용 (API 키 불필요)
monkey-collect run --steps 100 --input-mode random

# 단일 세션 변환 (raw → JSONL)
monkey-collect convert --session <dir> --output <path> --images-dir <dir>

# 전체 세션 일괄 변환
monkey-collect convert-all --raw-dir <dir> --output <path> --images-dir <dir>

# 세션에서 페이지 맵 빌드 + 시각화 (사후)
monkey-collect page-map --session <dir> [--threshold 0.85] [--no-open]

# 전체 세션 일괄 페이지 맵 빌드
monkey-collect page-map-all --raw-dir <dir> [--threshold 0.85]

# 파서 변경 후 XML 파생 파일 재생성 (raw → _parsed, _hierarchy, _encoded, _pretty)
monkey-collect regenerate [--raw-dir data/raw]
```

Android 앱은 `app/` 디렉토리에서 Gradle로 빌드 (compileSdk 34, minSdk 28).

### 수집 플로우
1. 터미널: `monkey-collect run` (서버 대기, 기본 다중 세션)
2. 에뮬레이터: MonkeyCollector 앱 → 설정(IP/Port) → Save & Ready → 권한 허용
3. 에뮬레이터: 타겟 앱 열기 → 플로팅 ▶ 버튼 클릭 → foreground 앱 자동 감지 → 수집 시작
4. ■ 버튼으로 세션 종료 → 다른 앱 열기 → ▶ 버튼 → 새 세션 자동 시작 (서버 재시작 불필요)
5. Ctrl+C로 서버 종료

**세션 이어서 저장**: 세션 디렉토리는 패키지명으로 저장된다 (`data/raw/{package}/`). 같은 앱을 다시 수집하면 항상 기존 세션에 데이터를 이어서 저장한다 (스크린샷/XML/이벤트/커버리지/비용 모두 append). PageGraph는 세션 종료 시 전체 XML로 재빌드. `--new-session` 플래그로 기존 데이터를 삭제하고 새 세션 강제 생성 가능.

## Architecture

```
Android App (Kotlin)  ←TCP→  Python Server
  CollectorService           CollectionServer (+ signal queue + SESSION_END)
  FloatingCollectorButton    Collector (orchestration + no-change retry + external_app recovery)
  ScreenStabilizer           SmartExplorer (weighted selection + element exclusion)
  TcpClient (bidirectional)  TextGenerator (LLM/Random input text strategy)
  MainActivity               AdbClient (action execution + activity discovery)
                             DataWriter (session storage, XML 5종 저장)
                             StructuredXmlParser (5-stage XML pipeline)
                             PageGraph (page map builder, parser 전처리 fingerprint)
                             GraphVisualizer (PyVis HTML)
                             ActivityCoverageTracker (activity coverage CSV)
                             CostTracker (LLM cost CSV)
                             Converter (encoded XML → ShareGPT JSONL)
```

**수집 루프**: 세션 시작 시 같은 앱의 기존 세션 자동 감지 → 있으면 resume (step/tracker 이어서), 없으면 새 세션 생성. App이 A11y 이벤트 감지 → ScreenStabilizer로 전환 확정 → 스크린샷+XML+Activity를 서버로 전송 → DataWriter가 XML 5종 저장 → SmartExplorer가 액션 선택 → ADB로 실행 → 반복. 세션 종료 시 전체 XML로 PageGraph 재빌드 (`build_graph_from_session()`). 화면 변화 없으면 N signal 전송 → no-change retry (max 3, element exclusion). first screen 보호: back 비활성화, tap으로 대체. external_app signal 시 server-side 능동 복구: return_to_app (1-3회) → recover (4+회) → 세션 종료 (10+회). 빈 UI tree 보호: 앱 로딩 중 빈 화면이면 back 대신 대기 후 재시도 (max 2회), external_app_count는 유효한 UI가 있는 화면에서만 리셋.

**TCP 프로토콜 (App→Server)**: `P`(타겟 패키지명), `S`(스크린샷 JPEG), `X`(XML+top_pkg+activity_name+target_pkg+is_first_screen), `E`(외부 앱 감지), `N`(화면 변화 없음), `F`(종료). 바이너리 데이터는 크기 prefixed. Activity명은 앱이 `TYPE_WINDOW_STATE_CHANGED` 이벤트에서 추출하여 전송.

**TCP 프로토콜 (Server→App)**: `\r\n` 구분 JSON. `{"type":"SESSION_END"}` — 서버가 세션 종료를 앱에 알림. 앱의 TcpClient reader 스레드가 수신하여 `stopCollection()` 호출. 세션 종료 시 서버가 자동 전송 + 소켓 close.

## Package Layout

`server/` 는 4-layer 서브패키지로 구성된다:

- **`server/domain/`** — 순수 도메인 모델 (I/O 의존 없음): actions, activity_coverage, cost_tracker, page_graph
- **`server/pipeline/`** — 수집 오케스트레이션: collector(facade) + session_manager + collection_loop + recovery + explorer + text_generator
- **`server/export/`** — 산출물 생성: converter, graph_visualizer
- **`server/infra/`** — 외부 시스템 어댑터: device/adb, network/server, storage/storage, xml/{ui_tree, parser/}

공개 API 는 `server/__init__.py` 에서 노출: `Collector, SmartExplorer, TextGenerator, CollectionServer, AdbClient, DataWriter, Converter, PageGraph, build_graph_from_session`.

## Key Modules

| Module | Role |
|--------|------|
| `server/pipeline/collector.py` | Collector facade. `run()` 단일 세션, `run_multi()` 다중 세션 (서버 유지). 내부 루프는 `session_manager` + `collection_loop` + `recovery` 로 위임 |
| `server/pipeline/session_manager.py` | 세션 생명주기: `wait_for_connection()`, `receive_target_package()`, `init_or_resume_session()` (같은 앱 기존 세션 자동 감지 → `resume_session()` / tracker `resume()`, `--new-session` 으로 기존 삭제), `finalize_session()` (send_session_end → finalize_session → `build_graph_from_session()` 재빌드 → HTML 시각화) |
| `server/pipeline/collection_loop.py` | 메인 수집 while-루프 + `CollectionState` dataclass. 시그널 디스패치 (`_handle_no_change`, `_handle_external_app`, `_process_xml_signal`). Activity 커버리지 기록, PageGraph 실시간 빌드, stuck page(MAX_SAME_PAGE_STEPS) 감지 |
| `server/pipeline/recovery.py` | 복구 헬퍼 + 상수 (`MAX_NO_CHANGE_RETRIES=3`, `MAX_EXTERNAL_APP_RETRIES=10`, `MAX_SAME_PAGE_STEPS=5`, `MAX_EMPTY_UI_RETRIES=2`). `tap_random_fallback()`, `describe_action_element()`, `safe_press_back()` |
| `server/pipeline/explorer.py` | `SmartExplorer`: 가중치 기반 랜덤 액션 선택 (tap 60%, swipe/back/input 10%, long_press 5%). element exclusion, first screen back 비활성화. TextGenerator 주입. `has_left_app()` / `return_to_app()` / `recover()` |
| `server/pipeline/text_generator.py` | InputText 생성 전략. `RandomTextGenerator` (하드코딩), `LLMTextGenerator` (OpenAI gpt-5-nano Responses API, reasoning: minimal, verbosity: low). API 실패 시 자동 fallback |
| `server/infra/network/server.py` | `CollectionServer`: TCP 서버, 바이너리 프로토콜 파싱 (X 메시지에 activity_name 포함). Queue 기반 change signal 대기. `reset_for_new_session()` 세션 간 상태 초기화. `send_session_end()` 앱에 종료 알림 |
| `server/infra/device/adb.py` | `AdbClient`: ADB 커맨드 래퍼 (자동 SDK 경로 탐색). `launch_app()` inner class `$` 이스케이프 처리. `get_current_activity()`, `get_declared_activities()` — Activity 커버리지용 |
| `server/infra/storage/storage.py` | `DataWriter`: 세션 디렉토리 구조 관리 (`data/raw/{package}/`), XML 5종 저장. `find_existing_session()`, `resume_session()`, `regenerate_xml_variants()` (파서 변경 후 raw XML 파생 파일 재생성) |
| `server/infra/xml/ui_tree.py` | uiautomator XML → `UIElement`/`UITree` 파싱 (SmartExplorer 액션 선택용) |
| `server/infra/xml/parser/` | 구조적 XML 파서 (`base.py`, `structured_parser.py`). 5단계 파이프라인: _reformat→_simplify→_clean→_renumber→pretty_xml. HTML-like 시맨틱 태그 변환. `_BUTTON_CLASSES`, `_LAYOUT_CLASSES` 매핑, scrollable=true → Scroll, 미등록 클래스 → div fallback |
| `server/export/converter.py` | 세션의 encoded XML (`_encoded.xml`) → ShareGPT JSONL 변환 |
| `server/export/graph_visualizer.py` | PyVis 기반 페이지 맵 HTML 시각화. Activity별 노드 색상, 전환 빈도별 엣지 너비. hierarchical (>15 노드) / forceAtlas2 레이아웃 |
| `server/domain/actions.py` | Action 데이터클래스 (Tap, Swipe, InputText, LongPress, PressBack, PressHome) |
| `server/domain/page_graph.py` | 페이지 맵 엔진. parser 전처리(_reformat+_simplify) 후 fingerprint로 고유 페이지 식별. `PageGraph` (dedup key: from_page+to_page+action_type). `build_graph_from_session()` 사후 재구축 |
| `server/domain/activity_coverage.py` | 앱별 Activity 방문 커버리지 추적. 세션별 `activity_coverage.csv`. `resume()` 기존 CSV append |
| `server/domain/cost_tracker.py` | LLM API 토큰 사용량/비용 추적. 세션별 `cost.csv`. `MODEL_PRICING` 딕셔너리. `resume()` 기존 CSV append |
| `server/cli.py` | CLI 진입점 (`server.cli:main`). `run`, `convert`, `convert-all`, `page-map`, `page-map-all`, `regenerate` 서브커맨드 |

## Android App (Kotlin)

`app/app/src/main/java/com/monkey/collector/` 하위:

- **CollectorService**: AccessibilityService. WINDOW_STATE/CONTENT_CHANGED 이벤트 디바운스(300ms) 후 ScreenStabilizer로 전환 확정. 플로팅 버튼 관리. first screen 플래그 전송. `TYPE_WINDOW_STATE_CHANGED`에서 현재 Activity 클래스명 추적 (`currentActivityName`). 외부 앱 감지 시 런처 판별(`LAUNCHER_PACKAGES` + `isLauncher()`) → `getLaunchIntentForPackage()` 또는 back으로 복구
- **FloatingCollectorButton**: TYPE_ACCESSIBILITY_OVERLAY 플로팅 START/STOP 버튼. foreground 앱을 타겟으로 자동 감지
- **ScreenStabilizer**: 저해상도(100px) 프레임 비교로 안정화 감지 (1.5% 임계값, 7연속 안정 프레임, 1000ms 초기 대기). first screen 감지 (5% 임계값). oscillation 감지: 커서 깜빡임 등 2~3프레임 교대 반복 패턴을 프레임 해시(8×8 grid) ring buffer로 감지하여 조기 안정화 판정. 타임아웃 시 lastStableFrame 업데이트 + hasVisualChange() 임계값 완화(5%)
- **TcpClient**: 양방향 TCP 통신. App→Server: P/S/X/E/N/F 프로토콜 (synchronized write, 3회 재시도). Server→App: reader 스레드에서 `\r\n` 구분 JSON 수신 (SESSION_END 등)
- **ScreenCapture**: API 30+ AccessibilityService.takeScreenshot() 래퍼. 5초 타임아웃 동기 캡처
- **BitmapComparator**: 픽셀 단위 비트맵 비교. 차이 비율(0.0~1.0) 반환
- **XmlDumper**: AccessibilityNodeInfo 트리 → uiautomator XML 변환
- **MediaProjectionHelper**: MediaProjection 권한 결과 싱글톤 브릿지 (Activity→Service)
- **MainActivity**: 설정(IP/Port) 저장 + MediaProjection 권한 요청 전용. 타겟 앱은 플로팅 버튼에서 자동 감지

## Data Format

**Raw 세션 구조**:
```
data/raw/{package}/
├── metadata.json           # session_id, package, started_at, completed_at, total_steps, external_app_events, resumed_at[]
├── screenshots/0000.png, 0001.png, ...
├── xml/
│   ├── 0000.xml                # raw uiautomator dump
│   ├── 0000_parsed.xml         # semantic HTML tags + bounds + index
│   ├── 0000_hierarchy.xml      # 구조만 (text/bounds/index 제거)
│   ├── 0000_encoded.xml        # bounds 제거, index만 (LLM 입력용)
│   └── 0000_pretty.xml         # encoded의 pretty-print
├── events.jsonl
├── activity_coverage.csv
├── cost.csv
├── page_graph.json         # 페이지 전환 그래프 (nodes + edges)
└── page_graph.html         # 인터랙티브 시각화 (PyVis)
```

**변환 출력**: ShareGPT 형식 JSONL. 연속 XML 쌍(before/after) + 액션으로 학습 예제 생성.

## Dependencies

Python: `loguru`, `Pillow`, `openai`, `python-dotenv`, `pyvis` (그 외 ADB/TCP/XML은 stdlib). Python ≥3.10 필요.
