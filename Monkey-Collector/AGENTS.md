# AGENTS.md

`Monkey-Collector/` 하위 프로젝트에서 작업하는 에이전트를 위한 가이드다.

## 현재 코드 기준 요약

- Python 쪽 진입점은 [`server/cli.py`](./server/cli.py) 의 `monkey-collect` CLI 다.
- 공개 API 는 [`server/__init__.py`](./server/__init__.py) 에서 export 된다.
- Android 앱 코드는 [`app/app/src/main/java/com/monkey/collector`](./app/app/src/main/java/com/monkey/collector) 아래에 있다.
- 서버 구조는 `domain`, `pipeline`, `export`, `xml` 4개 서브패키지 + 인프라 모듈 (`adb.py`, `tcp_server.py`, `storage.py`) 로 구성된다.

## 어디를 수정해야 하는가

- CLI 옵션이나 서브커맨드를 바꾸면 [`server/cli.py`](./server/cli.py) 와 [`tests/test_cli.py`](./tests/test_cli.py) 를 함께 수정한다. ADB 는 `AdbClient()` 를 인자 없이 생성하며, 내부에서 `ImplicitWorldModel` 이라는 이름의 AVD 를 자동 탐색해 해당 emulator serial 로 모든 명령을 고정한다 (상수 `REQUIRED_AVD_NAME` 은 [`server/adb.py`](./server/adb.py) 상단에 하드코드). AVD 이름을 바꿔야 한다면 이 상수와 관련 문서 / 테스트를 함께 수정한다.
- 수집 루프 동작은 [`server/pipeline/collector.py`](./server/pipeline/collector.py), [`server/pipeline/collection_loop.py`](./server/pipeline/collection_loop.py), [`server/pipeline/session_manager.py`](./server/pipeline/session_manager.py) 가 기준이다.
- 앱 목록 / 설치 상태 처리는 두 모듈로 분리되어 있다:
  - [`server/pipeline/app_catalog.py`](./server/pipeline/app_catalog.py): `catalog/apps.csv` 파싱과 category/priority/installed 필터. 새 필수 컬럼 추가는 `_REQUIRED_COLUMNS` 와 `AppJob` 을 동시에 수정. `installed` 는 optional 컬럼 — 누락된 CSV 는 자동으로 모두 `false` 로 해석된다.
  - [`server/pipeline/installed_sync.py`](./server/pipeline/installed_sync.py): `sync-installed` 서브커맨드의 백엔드. `catalog/apps.csv` 의 `installed` 컬럼만 in-place 로 덮어쓰므로 다른 필드를 건드리지 마라.
  - [`catalog/`](./catalog): 앱 카탈로그 자원. `apps.csv`, `apks/{pkg}.apk`, `download_apks.py` 가 한곳에 모여 있다. CLI 기본값은 `catalog/apps.csv` 를 가리키므로 CWD 가 프로젝트 루트라는 전제로 동작한다.
  - [`server/pipeline/reset.py`](./server/pipeline/reset.py): 수집 데이터 삭제 스코프 해소(`all` / `packages`)와 `shutil.rmtree` 실행. 순수 함수 (`resolve_targets`, `delete_targets`).
- 완료 앱 스킵 로직은 [`server/cli.py`](./server/cli.py) 의 `_resolve_run_packages` / `_load_completed_packages` 에 있다. `metadata.completed_at` 이 채워진 앱은 기본적으로 큐에서 제외되고, `--force` 로 우회한다. 이 규약이 바뀌면 `tests/test_run_resume.py` 를 함께 업데이트한다.
- 액션 선택 로직은 [`server/pipeline/explorer.py`](./server/pipeline/explorer.py) 와 [`tests/test_explorer.py`](./tests/test_explorer.py) 를 함께 본다.
- 텍스트 입력 생성은 [`server/pipeline/text_generator.py`](./server/pipeline/text_generator.py) 가 기준이며, 현재 OpenAI Responses API `gpt-5-nano` + random fallback 구조다.
- 세션 저장 형식은 [`server/storage.py`](./server/storage.py) 가 기준이다.
- XML 파싱 규약은 [`server/xml/ui_tree.py`](./server/xml/ui_tree.py), [`server/xml/structured_parser.py`](./server/xml/structured_parser.py) 를 본다.
- Android 측 전환 감지와 TCP 프로토콜은 [`CollectorService.kt`](./app/app/src/main/java/com/monkey/collector/CollectorService.kt), [`ScreenStabilizer.kt`](./app/app/src/main/java/com/monkey/collector/ScreenStabilizer.kt), [`TcpClient.kt`](./app/app/src/main/java/com/monkey/collector/TcpClient.kt) 에 있다.

## 작업 시 주의점

- 세션 디렉토리는 `data/raw/{package}/` 형식이다. timestamp 기반 새 디렉토리를 만들지 않는다.
- 기본 동작은 같은 앱 패키지의 기존 세션을 이어서 저장하는 것이다. `run` 커맨드의 `--new-session` 은 해당 앱 한 개만 초기화한다. 더 넓은 범위 삭제는 `monkey-collect reset` 을 사용한다.
- App -> Server signal 이름 `P`, `S`, `X`, `E`, `N`, `F` 와 Server -> App 제어 메시지 (`{"type":"START","package":...}`, `{"type":"SESSION_END"}`) 계약을 깨지 마라. Android 측 `CollectorService.beginStandby` 루프가 이 계약에 의존한다.
- 세션 전환 핸드셰이크: `SESSION_END` → 클라이언트 `F` 회신 + 소켓 close → 클라이언트 한 번 자동 재접속. Python 의 `CollectionServer.reset_for_new_session()` 은 큐/이벤트만 초기화하고 fresh 소켓은 보존해야 한다. 여기서 소켓을 닫으면 클라이언트가 두 번째 재접속을 하지 않아 두 번째 세션부터 `wait_for_connection` 이 전부 타임아웃한다.
- first screen 보호, no-change retry, external app recovery 는 collector 의 핵심 동작이다. 관련 상수는 [`server/pipeline/recovery.py`](./server/pipeline/recovery.py) 에 있다.
- `server/__init__.py` 의 공개 export 를 바꾸면 패키지 사용 코드와 문서도 같이 갱신한다.
- 저장 포맷을 바꾸면 converter, page-map, regenerate, 테스트를 함께 갱신해야 한다.

## 빠른 검증 포인트

- `pytest -q`
- `pytest -q tests/test_cli.py tests/test_collector.py tests/test_storage.py`
- `pytest -q tests/test_app_catalog.py tests/test_installed_sync.py tests/test_run_resume.py tests/test_reset.py`
- `python -m server.cli run --help`
- `python -m server.cli sync-installed --help`
- `python -m server.cli reset --help`
- `python -m server.cli page-map --help`

## 문서 동기화 원칙

- README 는 실제 운영 절차와 CLI 예시 중심으로 유지한다.
- ARCHITECTURE 는 현재 파일 구조와 TCP / storage 계약 중심으로 유지한다.
- CLI, 저장 구조, Android 서비스 흐름이 바뀌면 README, ARCHITECTURE, AGENTS 를 함께 수정한다.
