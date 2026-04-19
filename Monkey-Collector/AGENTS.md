# AGENTS.md

`Monkey-Collector/` 하위 프로젝트에서 작업하는 에이전트를 위한 가이드다.

## 현재 코드 기준 요약

- Python 쪽 진입점은 [`server/cli.py`](./server/cli.py) 의 `monkey-collect` CLI 다.
- 공개 API 는 [`server/__init__.py`](./server/__init__.py) 에서 export 된다.
- Android 앱 코드는 [`app/app/src/main/java/com/monkey/collector`](./app/app/src/main/java/com/monkey/collector) 아래에 있다.
- 서버 구조는 `domain`, `pipeline`, `export`, `infra` 4개 레이어로 분리되어 있다.

## 어디를 수정해야 하는가

- CLI 옵션이나 서브커맨드를 바꾸면 [`server/cli.py`](./server/cli.py) 와 [`tests/test_cli.py`](./tests/test_cli.py) 를 함께 수정한다.
- 수집 루프 동작은 [`server/pipeline/collector.py`](./server/pipeline/collector.py), [`server/pipeline/collection_loop.py`](./server/pipeline/collection_loop.py), [`server/pipeline/session_manager.py`](./server/pipeline/session_manager.py) 가 기준이다.
- 병렬 배치 수집 관련 작업은 4개 모듈로 분리되어 있다:
  - [`server/pipeline/app_catalog.py`](./server/pipeline/app_catalog.py): `apps.csv` 파싱과 category/priority 필터. 새 컬럼 추가는 `_REQUIRED_COLUMNS` 와 `AppJob` 을 동시에 수정.
  - [`server/infra/device/apk_installer.py`](./server/infra/device/apk_installer.py): APK 해결과 `adb install/uninstall`. 설치 상태 분기는 `InstallResult` enum 으로만 표현한다.
  - [`server/infra/device/avd.py`](./server/infra/device/avd.py): **raw `emulator` 바이너리 직접 호출**. 실제 CLI 호출은 `_run` 한 곳으로만 통과시키고 `Popen(emulator, …)` 은 별도. headless on/off 는 `AvdPool(headless=bool)` 로 전달, 기본은 창 표시.
  - [`server/pipeline/batch_collector.py`](./server/pipeline/batch_collector.py): 워커풀 스케줄러. `InstallerFactory` 와 `CollectorFactory` 로 주입받아 테스트에서 mock 가능.
- 운영 스크립트는 `scripts/` 에 분리. 런타임에 `monkey-collect` CLI 가 호출하지 않고 사전 준비용으로만 쓴다:
  - [`scripts/fetch_fdroid_apks.py`](./scripts/fetch_fdroid_apks.py): F-Droid 앱 자동 수집.
  - [`scripts/install_collector_to_avds.sh`](./scripts/install_collector_to_avds.sh): Collector APK 다중 AVD 병렬 설치.
- 액션 선택 로직은 [`server/pipeline/explorer.py`](./server/pipeline/explorer.py) 와 [`tests/test_explorer.py`](./tests/test_explorer.py) 를 함께 본다.
- 텍스트 입력 생성은 [`server/pipeline/text_generator.py`](./server/pipeline/text_generator.py) 가 기준이며, 현재 OpenAI Responses API `gpt-5-nano` + random fallback 구조다.
- 세션 저장 형식은 [`server/infra/storage/storage.py`](./server/infra/storage/storage.py) 가 기준이다.
- XML 파싱 규약은 [`server/infra/xml/ui_tree.py`](./server/infra/xml/ui_tree.py), [`server/infra/xml/parser/structured_parser.py`](./server/infra/xml/parser/structured_parser.py) 를 본다.
- Android 측 전환 감지와 TCP 프로토콜은 [`CollectorService.kt`](./app/app/src/main/java/com/monkey/collector/CollectorService.kt), [`ScreenStabilizer.kt`](./app/app/src/main/java/com/monkey/collector/ScreenStabilizer.kt), [`TcpClient.kt`](./app/app/src/main/java/com/monkey/collector/TcpClient.kt) 에 있다.

## 작업 시 주의점

- 세션 디렉토리는 `data/raw/{package}/` 형식이다. timestamp 기반 새 디렉토리를 만들지 않는다.
- 기본 동작은 같은 앱 패키지의 기존 세션을 이어서 저장하는 것이다. 새 세션 강제 생성은 `--new-session` 으로만 한다.
- App -> Server signal 이름 `P`, `S`, `X`, `E`, `N`, `F` 와 Server -> App `{"type":"SESSION_END"}` 계약을 깨지 마라.
- first screen 보호, no-change retry, external app recovery 는 collector 의 핵심 동작이다. 관련 상수는 [`server/pipeline/recovery.py`](./server/pipeline/recovery.py) 에 있다.
- `server/__init__.py` 의 공개 export 를 바꾸면 패키지 사용 코드와 문서도 같이 갱신한다.
- 저장 포맷을 바꾸면 converter, page-map, regenerate, 테스트를 함께 갱신해야 한다.

## 빠른 검증 포인트

- `pytest -q`
- `pytest -q tests/test_cli.py tests/test_collector.py tests/test_storage.py`
- `pytest -q tests/test_app_catalog.py tests/test_apk_installer.py tests/test_avd_pool.py tests/test_batch_collector.py`
- `python -m server.cli run --help`
- `python -m server.cli collect-batch --help`
- `python -m server.cli page-map --help`

## 문서 동기화 원칙

- README 는 실제 운영 절차와 CLI 예시 중심으로 유지한다.
- ARCHITECTURE 는 현재 파일 구조와 TCP / storage 계약 중심으로 유지한다.
- CLI, 저장 구조, Android 서비스 흐름이 바뀌면 README, ARCHITECTURE, AGENTS 를 함께 수정한다.
