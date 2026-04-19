# Monkey-Collector

Android GUI world model 학습용 데이터를 수집하는 App + Server 파이프라인이다. Android AccessibilityService 앱이 화면 전환을 감지해 screenshot 과 XML 을 보내고, Python 서버가 다음 action 을 선택해 ADB 로 실행한다.

## 개요

구성 요소:

- Android app: [`app/`](./app)
- Python server package: [`server/`](./server)
- CLI entrypoint: `monkey-collect`
- 테스트: [`tests/`](./tests)

현재 코드 기준 핵심 동작:

- App 이 screen stabilization 과 visual change 판정을 담당한다.
- Server 가 SmartExplorer 로 action 을 선택하고 raw session 을 저장한다.
- 세션 디렉토리는 `data/raw/{package}/` 형식이며, 기본 동작은 기존 세션 resume 이다.
- input text 생성은 OpenAI 기반 `api` 모드와 hardcoded `random` 모드를 지원한다. `OPENAI_API_KEY` 가 없으면 자동으로 random fallback 한다.

## 설치

### Python server

```bash
pip install -e .
```

선택 사항:

```bash
cp .env.example .env
```

`.env` 또는 환경변수에 `OPENAI_API_KEY` 를 넣으면 `--input-mode api` 에서 LLM 기반 입력 텍스트 생성을 사용한다.

추가 전제:

- Python 3.10+
- ADB 가 PATH 에 있거나 `ANDROID_HOME` 이 설정되어 있어야 한다

### Android app

```bash
cd app
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

설치 후 디바이스에서 AccessibilityService 를 활성화해야 한다.

## 빠른 시작

### 1. 서버 실행

```bash
monkey-collect run --steps 100
```

기본 동작:

- 다중 세션 모드
- 클라이언트가 target package 를 보내면 그 앱 기준으로 수집 시작
- 같은 패키지의 기존 세션이 있으면 이어서 저장

### 2. Android 앱에서 연결

1. Monkey Collector 앱에서 server IP / port 입력
2. Save 후 필요한 권한 허용
3. 수집할 앱을 foreground 로 띄움
4. 플로팅 START 버튼 클릭

### 3. 단일 세션 또는 새 세션 강제 시작

```bash
monkey-collect run --app com.android.calculator2 --single --steps 50
monkey-collect run --steps 100 --new-session
monkey-collect run --steps 100 --input-mode random
```

## CLI

### `run`

```bash
monkey-collect run --steps 100
monkey-collect run --app com.android.calculator2 --single --steps 50
```

주요 옵션:

- `--app`: 서버에서 target package 를 강제 지정한다. 생략하면 클라이언트가 보낸 package 를 사용한다.
- `--steps`: 세션당 최대 step 수
- `--seed`: explorer 랜덤 시드
- `--delay`: action 사이 대기 시간(ms)
- `--port`: TCP server port
- `--output`: raw session 저장 루트
- `--device`: ADB device serial
- `--input-mode`: `api` 또는 `random`
- `--single`: 단일 세션 모드
- `--new-session`: 기존 패키지 세션 삭제 후 새로 시작

### `collect-batch`

여러 AVD 를 사용해 `apps.csv` 기반으로 **카테고리별 / 우선순위별** 데이터 수집을 병렬 수행한다.

사전 준비:

1. **AVD 생성** — `avdmanager create avd` 로 원하는 만큼 미리 만든다.
   ```bash
   avdmanager create avd --name monkey-1 \
     --package "system-images;android-36.1;google_apis;x86_64" \
     --device pixel_9
   ```
   (pixel_5 / pixel_9 등 device profile 은 `avdmanager list device -c | grep pixel` 로 확인.)
2. **APK 수집** — `apks/{package_id}.apk` 형식으로 설치할 APK 를 넣는다. apps.csv 의 `package_id` 와 파일명이 일치해야 한다. 누락된 앱은 경고 후 skip 된다. F-Droid 앱은 `scripts/fetch_fdroid_apks.py` 로 자동 수집할 수 있다 (아래 참조).
3. **Collector 앱 빌드·설치** — `app/` Android 앱을 빌드한 뒤 각 AVD 에 설치한다. `scripts/install_collector_to_avds.sh` 로 AVD 여러 대에 병렬 설치 가능.
4. **KVM 가속 (Linux)** — x86_64 에뮬레이터는 `/dev/kvm` 접근 권한이 필요하다. 현재 사용자가 `kvm` 그룹에 없으면:
   ```bash
   sudo gpasswd -a $USER kvm   # 재로그인 필요 (또는 `newgrp kvm`)
   # 또는 임시: sudo setfacl -m u:$USER:rw /dev/kvm
   ```
5. **수집할 앱 foreground 진입 + START** — 기존 단일 수집 (`run`) 과 동일하게 각 AVD 에서 START 버튼을 한 번 눌러야 세션이 시작된다.

기본 사용:

```bash
monkey-collect collect-batch \
  --avds monkey-1,monkey-2 \
  --parallel 2 \
  --categories Shopping,Productivity \
  --priorities High,Medium \
  --apps-csv apps.csv \
  --apks-dir apks \
  --output data/raw \
  --steps 200
```

주요 옵션:

- `--avds`: 사전 생성된 AVD 이름의 콤마 구분 리스트 (필수)
- `--parallel`: 동시에 돌릴 워커 수 (기본 2, AVD 수 이하)
- `--categories`: apps.csv 의 `category` 값 중 수집할 것 (생략 시 전체)
- `--priorities`: `High,Medium,Low` 중 선택 (생략 시 전체)
- `--apps-csv`: apps.csv 경로 (기본 `apps.csv`)
- `--apks-dir`: APK 저장 디렉토리 (기본 `apks`)
- `--output`: 출력 루트 (기본 `data/raw`). 실제 세션은 `data/raw/{category}/{package}/` 에 저장
- `--steps`: 앱당 최대 step (기본 100)
- `--host-port-base`: AVD 0 이 사용할 호스트 포트 (기본 6000). AVD `i` 는 `base+i` 포트 사용. 내부에서 `adb reverse` 로 포워딩
- `--headless`: emulator 를 `-no-window -no-audio -no-boot-anim` 으로 실행 (기본: 창 표시)
- `--boot-timeout`: AVD 부팅 최대 대기 시간(초, 기본 180)
- `--uninstall-after`: 각 앱 수집 완료 후 APK 제거 (기본 미제거)
- `--new-session`: 같은 앱의 기존 세션 삭제 후 새로 시작
- `--dry-run`: 실제 수집 없이 실행 계획만 출력

저장 구조:

```
data/raw/
├── Shopping/
│   ├── in.amazon.mShop.android.shopping/
│   │   ├── metadata.json
│   │   ├── screenshots/
│   │   ├── xml/
│   │   └── events.jsonl
│   └── com.coupang.mobile/
│       └── ...
└── Productivity/
    └── com.google.android.keep/
        └── ...
```

**주의**: PlayStore/F-Droid 에 따른 자동 다운로드는 없다. APK 수집 및 저작권 준수는 운영자 책임이다.

## 운영 스크립트 (`scripts/`)

배치 수집을 위한 사전 준비를 자동화한다. 모두 `monkey-collect` 엔트리와 독립적으로 실행할 수 있다.

### `scripts/fetch_fdroid_apks.py`

`apps.csv` 의 `source=F-Droid` 앱들을 `apks/{package_id}.apk` 로 자동 다운로드한다. PlayStore / System 앱과 F-Droid 인덱스에 없는 package_id 는 `apks/MISSING.md` 에 기록된다.

```bash
python scripts/fetch_fdroid_apks.py           # 전체
python scripts/fetch_fdroid_apks.py --limit 1 # smoke test
python scripts/fetch_fdroid_apks.py --force   # 이미 받은 것도 재다운로드
```

내부적으로 `index-v2.json` (~50MB) 를 한 번 받아 `/tmp/monkey-fdroid-index-v2.json` 에 캐시하고, 각 package 의 최신 `versionCode` 에 해당하는 APK 파일명을 인덱스에서 직접 읽어 `https://f-droid.org/repo/{file}` 를 내려받는다. multi-variant APK (per-ABI 등) 도 정확히 해결된다.

### `scripts/install_collector_to_avds.sh`

AVD 여러 대를 **병렬로** 부팅 (`-no-window -no-audio -no-boot-anim`) → `adb install -r` → `adb emu kill` 까지 한 번에 처리한다.

```bash
# 사전: cd app && ./gradlew assembleDebug
bash scripts/install_collector_to_avds.sh \
  app/app/build/outputs/apk/debug/app-debug.apk \
  monkey-1 monkey-2 monkey-3 monkey-4
# 로그: /tmp/monkey-install-logs/monkey-{1..4}.log(.emu|.install)
```

한 AVD 의 실패는 다른 AVD 를 막지 않는다. 최종 종료 코드는 하나라도 실패하면 1.

### `convert`

```bash
monkey-collect convert \
  --session data/raw/com.example.app \
  --output data/processed/gui-model_stage1.jsonl \
  --images-dir data/processed/images
```

### `convert-all`

```bash
monkey-collect convert-all \
  --raw-dir data/raw \
  --output data/processed/gui-model_stage1.jsonl \
  --images-dir data/processed/images
```

### `page-map`

```bash
monkey-collect page-map --session data/raw/com.example.app
monkey-collect page-map --session data/raw/com.example.app --threshold 0.9 --no-open
```

### `page-map-all`

```bash
monkey-collect page-map-all --raw-dir data/raw --no-open
```

### `regenerate`

```bash
monkey-collect regenerate --raw-dir data/raw
```

raw XML 을 기준으로 `_parsed.xml`, `_hierarchy.xml`, `_encoded.xml`, `_pretty.xml` 를 다시 만든다.

## 저장 구조

기본 raw session 구조:

```
data/raw/{package}/
├── metadata.json
├── screenshots/
├── xml/
├── events.jsonl
├── activity_coverage.csv
├── cost.csv
├── page_graph.json
└── page_graph.html
```

`xml/` 아래에는 raw XML 과 함께 다음 파생 파일이 저장된다.

- `{step}_parsed.xml`
- `{step}_hierarchy.xml`
- `{step}_encoded.xml`
- `{step}_pretty.xml`

## 코드 읽기 시작점

- [`server/cli.py`](./server/cli.py): 실제 CLI
- [`server/pipeline/collector.py`](./server/pipeline/collector.py): 수집 진입점
- [`server/pipeline/explorer.py`](./server/pipeline/explorer.py): action selection
- [`server/infra/storage/storage.py`](./server/infra/storage/storage.py): 세션 포맷
- [`app/app/src/main/java/com/monkey/collector`](./app/app/src/main/java/com/monkey/collector): Android 앱

구조 설명은 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 작업 규칙은 [`AGENTS.md`](./AGENTS.md) 를 본다.
