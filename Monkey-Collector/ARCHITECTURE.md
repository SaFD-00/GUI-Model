# Monkey-Collector Architecture

`Monkey-Collector` 는 Android AccessibilityService 앱과 Python 서버를 조합해 GUI world model 학습용 데이터를 수집하는 파이프라인이다. 현재 코드는 "전환 감지는 App, 액션 선택과 저장은 Server" 구조를 기준으로 구현되어 있다.

## 1. 시스템 개요

### 역할 분리

- Android App
  - foreground 앱 감지
  - 화면 안정화 판단
  - screenshot 및 XML dump 생성
  - TCP 로 신호와 payload 전송
- Python Server
  - TCP 수신
  - XML 파싱
  - 다음 action 선택
  - ADB 실행
  - raw session 저장
  - page map 및 JSONL 변환

### 핵심 설계 포인트

- 전환 감지는 App 의 [`ScreenStabilizer.kt`](./app/app/src/main/java/com/monkey/collector/ScreenStabilizer.kt) 에서 수행한다.
- no-change, first screen, external app recovery 는 server collection loop 에서 처리한다.
- 세션 디렉토리는 패키지명 기반 `data/raw/{package}/` 이고, 기본 동작은 resume 이다.

## 2. 컴포넌트 구조

### Android App

경로: [`app/app/src/main/java/com/monkey/collector`](./app/app/src/main/java/com/monkey/collector)

- `CollectorService.kt`
  - AccessibilityService 본체
  - foreground package / activity 추적
  - screen change 발생 시 screenshot + XML 전송
  - external app 감지 및 client-side 복구
- `FloatingCollectorButton.kt`
  - START / STOP overlay
  - START 시 현재 foreground app 을 타겟으로 사용
- `ScreenStabilizer.kt`
  - 저해상도 프레임 비교
  - 안정화 대기와 시각 변화 판정
  - first screen 판정
- `BitmapComparator.kt`
  - 프레임 diff 계산
- `ScreenCapture.kt`
  - `AccessibilityService.takeScreenshot()` 래퍼
- `XmlDumper.kt`
  - Accessibility tree -> raw XML
- `TcpClient.kt`
  - App -> Server `P/S/X/E/N/F`
  - Server -> App JSON control message 수신
- `MainActivity.kt`
  - IP / Port 설정
  - MediaProjection 권한 브리지
- `MediaProjectionHelper.kt`
  - Activity 와 Service 사이 권한 데이터 전달

### Python Server

경로: [`server/`](./server)

- `domain/`
  - [`actions.py`](./server/domain/actions.py): Action dataclass 들
  - [`activity_coverage.py`](./server/domain/activity_coverage.py): Activity coverage CSV
  - [`cost_tracker.py`](./server/domain/cost_tracker.py): LLM 비용 추적 CSV
  - [`page_graph.py`](./server/domain/page_graph.py): 페이지 그래프 생성
- `pipeline/`
  - [`collector.py`](./server/pipeline/collector.py): collector facade
  - [`session_manager.py`](./server/pipeline/session_manager.py): session init/resume/finalize
  - [`collection_loop.py`](./server/pipeline/collection_loop.py): 메인 루프
  - [`recovery.py`](./server/pipeline/recovery.py): retry / recovery 상수와 helper
  - [`explorer.py`](./server/pipeline/explorer.py): SmartExplorer
  - [`text_generator.py`](./server/pipeline/text_generator.py): random 또는 OpenAI 기반 입력 텍스트 생성
- `infra/`
  - [`device/adb.py`](./server/infra/device/adb.py): ADB wrapper
  - [`network/server.py`](./server/infra/network/server.py): TCP 서버와 signal queue
  - [`storage/storage.py`](./server/infra/storage/storage.py): raw session 저장 및 XML variant 재생성
  - [`xml/ui_tree.py`](./server/infra/xml/ui_tree.py): action selection 용 UI tree
  - [`xml/parser/structured_parser.py`](./server/infra/xml/parser/structured_parser.py): 구조적 XML parser
- `export/`
  - [`converter.py`](./server/export/converter.py): raw session -> ShareGPT JSONL
  - [`graph_visualizer.py`](./server/export/graph_visualizer.py): page graph HTML 시각화

### Sweep 수집 레이어

여러 AVD 에서 `apps.csv` 기반 카테고리별 수집을 **순차 실행**하기 위한 상위 레이어. 한 번에 AVD 한 대만 부팅한다.

- `server/pipeline/app_catalog.py`
  - `AppCatalog`: stdlib csv 로 `apps.csv` 파싱, BOM/대소문자 정규화
  - `AppJob`: frozen dataclass (category, sub_category, app_name, package_id, source, priority, notes)
  - `filter(categories, priorities)`: case-insensitive 필터
- `server/infra/device/apk_installer.py`
  - `ApkResolver`: `{apks_dir}/{package_id}.apk` 경로 해결
  - `ApkInstaller`: `pm list packages` 로 기설치 확인 후 `adb install -r`, 결과를 `InstallResult` (INSTALLED / ALREADY / MISSING_APK / FAILED) 로 반환
  - `uninstall`: `adb uninstall <pkg>` subprocess 래퍼
- `server/infra/device/avd.py`
  - **raw `emulator` 바이너리 직접 호출** (`$ANDROID_HOME/emulator/emulator`) — `android emulator start` 래퍼는 창 플래그 등 raw 옵션을 넘길 수 없기 때문
  - `AvdPool.start_one(name, *, index)` / `stop(handle)` 로 한 번에 한 AVD 의 라이프사이클을 관리
  - AVD `index` 의 콘솔 포트는 `console_port_base + 2*index` (기본 5554) → serial 은 결정적 `emulator-{console_port}` (별도 discover 불필요)
  - `headless=True` 일 때 `-no-window -no-audio -no-boot-anim` 추가 (기본은 창 표시)
  - `_wait_for_boot`: `getprop sys.boot_completed` 폴링
  - `AvdPool.provision`: `adb reverse`, `enabled_accessibility_services`, `accessibility_enabled`, overlay appops 를 일괄 적용
  - context manager 로 `__exit__` 에서 아직 살아 있는 handle 정리
- `server/pipeline/sweep.py`
  - `Sweep`: AVD 순회 기반 순차 스케줄러. job 은 AVD 수 기준 round-robin 으로 사전 배분되고, 각 AVD 는 본인 몫을 순차 처리.
  - 의존성은 factory 주입 (`InstallerFactory`, `CollectorFactory`) — 테스트 용이.
  - `JobResult(job, avd_name, install_result, session_id, error, skip_reason)`. `skipped` 는 MISSING_APK/FAILED 또는 `skip_reason` 이 채워진 경우, `succeeded` 는 session_id 존재 + error 없음.
  - 기본 동작은 **resume-skip**: `{output}/{category}/{package}/metadata.json` 의 `completed_at` 이 채워진 앱은 `skip_reason="already_complete"` 로 건너뜀. `run(force=True)` (CLI `--force`) 로 우회.
- `server/pipeline/reset.py`
  - `resolve_targets(output_dir, all_, categories, packages, apps_csv, priorities)`: 삭제 스코프를 기존 디렉토리 경로 리스트로 해소. 우선순위는 `all_ → apps_csv → packages → categories`.
  - `delete_targets(targets, dry_run)`: `shutil.rmtree` 로 삭제하고 삭제 개수 반환. `dry_run=True` 면 로그만.

실행 흐름 (sweep):

```
AppCatalog.load(apps.csv)
  -> filter(categories, priorities)
  -> partition completed vs pending (unless --force)   # resume-skip
  -> jobs: list[AppJob]
for idx, name in enumerate(avd_names):
  assigned = jobs[idx::len(avd_names)]           # round-robin 분배
  handle = AvdPool.start_one(name, index=idx)    # 이 AVD 만 부팅
  try:
    AvdPool.provision(handle)                    # reverse + a11y + overlay
    installer = installer_factory(handle)
    for job in assigned:
      ApkInstaller.install(job)                  # MISSING/FAILED → JobResult 후 continue
      Collector(handle.serial, handle.host_port, base=output/category)
        .run(job.package_id)                     # 단일 세션 수집
      (opt) ApkInstaller.uninstall()
  finally:
    AvdPool.stop(handle)                         # 다음 AVD 부팅 전 반드시 종료
```

네트워크: 현재 부팅된 AVD 에 `adb -s <serial> reverse tcp:<port> tcp:<port>` 를 걸어 Collector app 의 TcpClient 가 `localhost:<port>` 로 보내는 패킷을 호스트의 `CollectionServer` 로 포워딩한다. AVD 마다 index 기반 결정적 포트(`host_port_base + index`)가 부여된다.

### 운영 스크립트

배치 수집을 쓰기 전 단계에서 쓰이는 도구들. 런타임 파이프라인과 분리되어 있다.

- [`scripts/fetch_fdroid_apks.py`](./scripts/fetch_fdroid_apks.py) — F-Droid `index-v2.json` 을 받아 `apps.csv` 의 F-Droid 앱들을 `apks/{package_id}.apk` 로 다운로드. PlayStore/System/실패 항목은 `apks/MISSING.md` 에 정리.
- [`scripts/install_collector_to_avds.sh`](./scripts/install_collector_to_avds.sh) — Collector debug APK 를 단일 AVD 에 설치(부팅 → `adb install -r` → `adb emu kill`). 여러 AVD 면 AVD 마다 한 번씩 호출.

## 3. 데이터 흐름

### 수집 루프

```
Android AccessibilityEvent
  -> ScreenStabilizer 안정화 판단
  -> no-change 이면 N signal
  -> 외부 앱 감지면 E signal
  -> 변화가 있으면 screenshot + XML + metadata 전송
  -> Python server 가 latest signal 소비
  -> XML parse
  -> SmartExplorer 가 action 선택
  -> ADB 실행
  -> screenshot/XML/event 저장
  -> 다음 step 반복
```

### TCP 프로토콜

App -> Server:

- `P`: target package
- `S`: screenshot payload
- `X`: XML + activity + package metadata
- `E`: external app signal
- `N`: no-change signal
- `F`: finish signal

Server -> App:

- `{"type":"SESSION_END"}` newline-delimited JSON

`CollectionServer` 는 signal queue 를 사용해 최신 signal 기준으로 collection loop 를 진행한다.

## 4. 세션 관리와 복구

### 세션 라이프사이클

- 기본 저장 위치는 `data/raw/{package}/` (sweep 은 `data/raw/{category}/{package}/`)
- 동일 패키지에 `metadata.json` 이 있으면 resume
- `run --new-session` 은 해당 앱 세션을 삭제하고 새로 시작
- `sweep` 은 `completed_at` 이 채워진 앱을 기본적으로 건너뜀 (`--force` 로 우회)
- `reset` 서브커맨드로 범위 단위 (all / categories / packages / apps-csv) 일괄 삭제 가능
- 세션 종료 시 metadata 업데이트, page graph 재빌드, HTML 시각화 생성

### 주요 복구 규칙

[`server/pipeline/recovery.py`](./server/pipeline/recovery.py) 기준 상수:

- `MAX_NO_CHANGE_RETRIES = 3`
- `MAX_EXTERNAL_APP_RETRIES = 10`
- `MAX_SAME_PAGE_STEPS = 5`
- `MAX_EMPTY_UI_RETRIES = 2`

주요 동작:

- no-change 시 이전에 실패한 element 를 exclusion 하고 재선택
- first screen 에서는 back 을 금지하고 tap fallback 사용
- external app 시 `return_to_app()` 후 필요하면 `recover()` 수행
- 빈 UI tree 가 반복되면 대기 후 재시도

## 5. 저장 포맷

세션별 기본 구조:

```
data/raw/{package}/
├── metadata.json
├── screenshots/
│   └── 0000.png
├── xml/
│   ├── 0000.xml
│   ├── 0000_parsed.xml
│   ├── 0000_hierarchy.xml
│   ├── 0000_encoded.xml
│   └── 0000_pretty.xml
├── events.jsonl
├── activity_coverage.csv
├── cost.csv
├── page_graph.json
└── page_graph.html
```

`DataWriter.save_xml()` 와 `regenerate_xml_variants()` 는 raw XML 에서 아래 파생 파일을 만든다.

- `_parsed.xml`: semantic HTML tags + bounds + index
- `_hierarchy.xml`: text / bounds / index 제거
- `_encoded.xml`: bounds 제거, index 유지
- `_pretty.xml`: encoded XML pretty-print

## 6. CLI 와 공개 API

### CLI

[`server/cli.py`](./server/cli.py) 가 아래 서브커맨드를 제공한다.

- `run`
- `sweep`
- `reset`
- `convert`
- `convert-all`
- `page-map`
- `page-map-all`
- `regenerate`

### 공개 API

[`server/__init__.py`](./server/__init__.py) 는 아래 주요 타입을 export 한다.

- `Collector`
- `Sweep`, `JobResult`
- `AppCatalog`, `AppJob`
- `ApkInstaller`, `ApkResolver`, `InstallResult`
- `AvdPool`, `AvdHandle`
- `SmartExplorer`
- `TextGenerator`
- `RandomTextGenerator`
- `LLMTextGenerator`
- `CollectionServer`
- `AdbClient`
- `DataWriter`
- `Converter`
- `PageGraph`
- `build_graph_from_session`
